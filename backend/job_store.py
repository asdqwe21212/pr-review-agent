"""Job persistence layer with pluggable backends."""
import os
import json
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional


class InMemoryJobStore:
    """Ephemeral dict-based store (original behaviour)."""

    def __init__(self):
        self._jobs: dict = {}
        self._lock = threading.Lock()

    def create(self, job_id: str, data: dict) -> None:
        with self._lock:
            self._jobs[job_id] = {"job_id": job_id, **data}

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, updates: dict) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(updates)

    def list_all(self, repo: Optional[str] = None, since: Optional[float] = None) -> list:
        with self._lock:
            jobs = list(self._jobs.values())
            if repo:
                jobs = [j for j in jobs if j.get("repo") == repo]
            if since is not None:
                jobs = [j for j in jobs if j.get("_created_ts", 0) >= since]
            return sorted(jobs, key=lambda j: j.get("_created_ts", 0), reverse=True)

    def list_by_status(self, status: str) -> list:
        with self._lock:
            return [j for j in self._jobs.values() if j.get("status") == status]


class FileJobStore(InMemoryJobStore):
    """JSONL-file-backed store – survives restarts, zero extra dependencies."""

    def __init__(self, path: Optional[str] = None):
        super().__init__()
        if path is None:
            data_dir = Path(__file__).resolve().parent.parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            path = str(data_dir / "jobs.jsonl")
        self.path = Path(path)
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        job = json.loads(line)
                        # Purge jobs older than 7 days
                        created = job.get("created_at", "")
                        if created:
                            try:
                                dt = datetime.fromisoformat(created)
                                if datetime.now(timezone.utc) - dt > timedelta(days=7):
                                    continue
                            except (ValueError, TypeError):
                                pass
                        self._jobs[job["job_id"]] = job
                    except json.JSONDecodeError:
                        continue

    def _persist(self):
        with self._lock:
            with open(self.path, "w", encoding="utf-8") as f:
                for job in self._jobs.values():
                    f.write(json.dumps(job, ensure_ascii=False) + "\n")

    def create(self, job_id: str, data: dict) -> None:
        super().create(job_id, data)
        self._persist()

    def update(self, job_id: str, updates: dict) -> None:
        super().update(job_id, updates)
        self._persist()


class RedisJobStore(InMemoryJobStore):
    """Redis-backed store for multi-process deployments."""

    def __init__(self, redis_url: Optional[str] = None):
        super().__init__()
        redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        import redis  # optional dependency
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._ttl = 7 * 24 * 3600  # 7 days

    def create(self, job_id: str, data: dict) -> None:
        """创建任务并建立索引"""
        key = f"pr_review:job:{job_id}"
        pipe = self.redis.pipeline()
        pipe.hset(key, mapping={"job_id": job_id, **data})
        pipe.expire(key, self._ttl)
        pipe.zadd("pr_review:jobs_index", {job_id: data.get("_created_ts", 0)})
        pipe.sadd(f"pr_review:status:{data.get('status', 'pending')}", job_id)
        pipe.sadd(f"pr_review:repo:{data.get('repo', 'unknown')}", job_id)
        pipe.execute()

    def get(self, job_id: str) -> Optional[dict]:
        data = self.redis.hgetall(f"pr_review:job:{job_id}")
        return data if data else None

    def update(self, job_id: str, updates: dict) -> None:
        """更新任务状态并维护索引"""
        old_job = self.get(job_id) or {}
        old_status = old_job.get("status")
        new_status = updates.get("status")
        
        key = f"pr_review:job:{job_id}"
        self.redis.hset(key, mapping=updates)
        
        # 更新状态索引
        if old_status and new_status and old_status != new_status:
            self.redis.srem(f"pr_review:status:{old_status}", job_id)
            self.redis.sadd(f"pr_review:status:{new_status}", job_id)

    def list_by_status(self, status: str) -> list:
        """使用索引快速查询"""
        job_ids = self.redis.smembers(f"pr_review:status:{status}")
        jobs = []
        for jid in job_ids:
            data = self.redis.hgetall(f"pr_review:job:{jid}")
            if data:
                jobs.append(data)
        return jobs

    def list_all(self, repo: Optional[str] = None, since: Optional[float] = None) -> list:
        ids = self.redis.zrevrange("pr_review:jobs_index", 0, -1)
        jobs = []
        for jid in ids:
            data = self.redis.hgetall(f"pr_review:job:{jid}")
            if not data:
                continue
            if repo and data.get("repo") != repo:
                continue
            if since is not None and float(data.get("_created_ts", 0)) < since:
                continue
            jobs.append(data)
        return jobs

    def list_by_status(self, status: str) -> list:
        return [j for j in self.list_all() if j.get("status") == status]


# --- Singleton factory ---
_store: Optional[InMemoryJobStore] = None
_lock = threading.Lock()


def get_job_store() -> InMemoryJobStore:
    global _store
    if _store is not None:
        return _store
    with _lock:
        if _store is not None:
            return _store
        if os.getenv("REDIS_URL"):
            try:
                _store = RedisJobStore()
                return _store
            except Exception:
                pass  # fall through to FileJobStore
        _store = FileJobStore()
        return _store


def reset_job_store():
    """Reset singleton (useful for testing)."""
    global _store
    _store = None

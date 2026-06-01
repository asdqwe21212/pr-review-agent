"""
FastAPI server for PR Review Agent
Provides REST API + serves the dashboard UI
"""
import os
import json
import asyncio
import logging
import secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
import uvicorn

from agent import PRReviewAgent
from webhook import webhook_router
from job_store import get_job_store
from rate_limit import limiter
from logging_config import configure_logging
from token_tracker import get_token_tracker
from metrics import get_review_metrics
from metrics_middleware import PrometheusMiddleware, get_metrics_response

configure_logging()
logger = logging.getLogger(__name__)

# arq queue connection pool
_queue_pool = None

async def get_queue():
    """获取 arq 队列连接"""
    global _queue_pool
    if _queue_pool is None:
        from arq import create_pool
        from arq.connections import RedisSettings
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _queue_pool = await create_pool(
            RedisSettings.from_dsn(redis_url)
        )
    return _queue_pool

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="PR Review Agent", version="1.0.0")

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS - configurable origins
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics middleware
app.add_middleware(PrometheusMiddleware)

# API Key Authentication (optional)
API_KEY = os.getenv("API_KEY")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Optional API key authentication. Set API_KEY env var to enable."""
    if API_KEY and request.url.path.startswith("/api/"):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") or auth_header[7:] != API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)

# Mount webhook router
app.include_router(webhook_router)

# Persisted job store
job_store = get_job_store()


class ReviewRequest(BaseModel):
    repo: str          # e.g. "owner/repo-name"
    pr_number: int
    post_comment: bool = True


class JobStatus(BaseModel):
    job_id: str
    status: str        # pending | running | done | error
    created_at: str
    result: Optional[dict] = None
    error: Optional[str] = None


def get_agent() -> PRReviewAgent:
    github_token = os.getenv("GITHUB_TOKEN")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if not github_token or not anthropic_key:
        raise HTTPException(status_code=500, detail="Missing GITHUB_TOKEN or ANTHROPIC_API_KEY env vars")
    return PRReviewAgent(github_token, anthropic_key)


async def run_review_job(job_id: str, repo: str, pr_number: int, post_comment: bool):
    """Background task to run the review."""
    job_store.update(job_id, {"status": "running"})
    try:
        agent = get_agent()
        result = await asyncio.to_thread(
            agent.review_pr, repo, pr_number, post_comment
        )
        job_store.update(job_id, {"status": "done", "result": result})
        logger.info(f"Job {job_id} completed — score: {result['review'].get('overall_score', 'N/A')}")
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        job_store.update(job_id, {"status": "error", "error": str(e)})


@app.post("/api/review", response_model=JobStatus)
@limiter.limit("10/minute")
async def start_review(req: ReviewRequest, request: Request, background_tasks: BackgroundTasks):
    """Start an async PR review job."""
    job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{req.pr_number}_{secrets.token_hex(4)}"
    now = datetime.now(timezone.utc)
    job_data = {
        "job_id": job_id,
        "status": "pending",
        "created_at": now.isoformat(),
        "repo": req.repo,
        "pr_number": req.pr_number,
        "result": None,
        "error": None,
        "_created_ts": now.timestamp(),
    }
    job_store.create(job_id, job_data)
    
    # 尝试使用 arq 队列
    try:
        queue = await get_queue()
        await queue.enqueue_job(
            "review_task",
            job_id,
            req.repo,
            req.pr_number,
            req.post_comment,
            _job_id=job_id,
        )
        job_store.update(job_id, {"status": "queued"})
        logger.info(f"Job {job_id} queued for {req.repo}#{req.pr_number}")
    except Exception as e:
        # 降级到 BackgroundTasks
        logger.warning(f"Failed to queue job, falling back to BackgroundTasks: {e}")
        background_tasks.add_task(run_review_job, job_id, req.repo, req.pr_number, req.post_comment)
        logger.info(f"Job {job_id} created for {req.repo}#{req.pr_number}")
    
    return job_data


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    """Poll job status."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs")
async def list_jobs(repo: Optional[str] = None, days: int = 7):
    """List all jobs, optionally filtered by repo and time window."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() if days > 0 else None
    return job_store.list_all(repo=repo, since=since)


@app.get("/api/stats")
async def get_stats(days: int = 7):
    """Aggregate stats across all jobs within the time window."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    all_jobs = job_store.list_all(since=since)
    done = [j for j in all_jobs if j.get("status") == "done"]
    total_tokens = 0
    for j in done:
        result = j.get("result") or {}
        usage = result.get("token_usage") or {}
        total_tokens += usage.get("input", 0) + usage.get("output", 0)
    scores = [
        j["result"]["review"]["overall_score"]
        for j in done if j.get("result") and j["result"].get("review", {}).get("overall_score")
    ]
    return {
        "total_reviews": len(all_jobs),
        "completed": len(done),
        "errors": len([j for j in all_jobs if j.get("status") == "error"]),
        "running": len([j for j in all_jobs if j.get("status") == "running"]),
        "total_tokens": total_tokens,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "time_window_days": days,
    }


# --- Metrics & Monitoring endpoints ---

@app.get("/api/metrics")
async def get_metrics(days: int = 7):
    """Combined metrics: quality + throughput + token budget."""
    metrics = get_review_metrics()
    return metrics.combined_report(days)


@app.get("/api/metrics/tokens")
async def get_token_metrics():
    """Token usage: weekly budget, daily breakdown, per-review stats."""
    tracker = get_token_tracker()
    return {
        "weekly": tracker.weekly_usage(),
        "daily": tracker.daily_usage(7),
        "per_review": tracker.per_review_stats(),
    }


@app.get("/api/metrics/quality")
async def get_quality_metrics(days: int = 7):
    """Review quality metrics: scores, severity, approval rate."""
    metrics = get_review_metrics()
    return metrics.quality_summary(days)


@app.get("/api/metrics/throughput")
async def get_throughput(days: int = 7):
    """Throughput metrics: reviews per day, files reviewed."""
    metrics = get_review_metrics()
    return metrics.throughput(days)


@app.get("/api/health")
async def health_check():
    """增强的健康检查"""
    checks = {
        "api": "ok",
        "redis": "ok",
        "queue": "ok",
    }
    
    # 检查 Redis 连接
    try:
        if hasattr(job_store, 'redis'):
            job_store.redis.ping()
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"
    
    # 检查队列
    try:
        await get_queue()
    except Exception as e:
        checks["queue"] = f"error: {str(e)}"
    
    all_ok = all(v == "ok" for v in checks.values())
    
    return {
        "status": "healthy" if all_ok else "degraded",
        "version": "1.1.0",
        "checks": checks,
    }


@app.get("/api/health/ready")
async def readiness():
    """Kubernetes readiness probe"""
    try:
        if hasattr(job_store, 'redis'):
            job_store.redis.ping()
    except Exception:
        raise HTTPException(503, detail="Redis unavailable")
    return {"ready": True}


@app.get("/api/health/live")
async def liveness():
    """Kubernetes liveness probe"""
    return {"alive": True}


@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点"""
    return get_metrics_response()


# Serve frontend
@app.get("/")
async def serve_frontend():
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)

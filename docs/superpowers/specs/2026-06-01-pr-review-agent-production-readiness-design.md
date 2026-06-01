# PR Review Agent - 生产就绪改造设计规格

## 文档信息

- **日期：** 2026-06-01
- **版本：** 1.0.0
- **状态：** 已批准
- **作者：** AI Assistant

---

## 1. 概述

### 1.1 背景

PR Review Agent 是一个基于 Claude API 的多轮推理代码审查系统，旨在帮助团队处理每周 50+ 个 PR 的审查需求。当前系统存在若干稳定性和可扩展性问题，需要进行生产就绪改造。

### 1.2 目标

- 消除已知的崩溃风险和兼容性问题
- 支持 50+ 人团队的高并发使用
- 提供生产级的可观测性和监控能力
- 实现高可用和自动重试机制

### 1.3 范围

本设计涵盖以下六个模块的改进：

1. 关键 Bug 修复与基础加固
2. 异步任务队列改造
3. Redis 存储后端增强
4. 请求去重与结果缓存
5. 结构化日志与监控
6. 生产级特性

---

## 2. 关键 Bug 修复与基础加固

### 2.1 修复 datetime 弃用警告

**问题：** `job_store.py` 和 `token_tracker.py` 使用已弃用的 `datetime.utcnow()`（Python 3.12+）

**修复方案：**
- 将所有 `datetime.utcnow()` 替换为 `datetime.now(timezone.utc)`
- 更新相关的时间比较逻辑

**影响文件：**
- `backend/job_store.py` - `FileJobStore._load()` 方法
- `backend/token_tracker.py` - 多处使用

### 2.2 修复前端轮询内存泄漏

**问题：** `frontend/index.html` 中 `refreshJobs` 的 setInterval 在长时间运行后未正确清理

**修复方案：**
```javascript
let pollController = null;

async function startPolling(jobId) {
  stopPolling();
  pollController = new AbortController();
  const maxRetries = 60; // 最多轮询5分钟
  let retries = 0;
  
  while (retries < maxRetries && !pollController.signal.aborted) {
    await refreshJobs();
    const job = await api(`/api/jobs/${jobId}`);
    if (job.status === 'done' || job.status === 'error') break;
    await new Promise(r => setTimeout(r, 2500));
    retries++;
  }
}

function stopPolling() {
  pollController?.abort();
  pollController = null;
}
```

### 2.3 改进 asyncio 事件循环处理

**问题：** `server.py` 中 `asyncio.get_event_loop()` 在无运行循环时抛出 RuntimeError

**修复方案：**
```python
# 使用 asyncio.to_thread() 替代
result = await asyncio.to_thread(agent.review_pr, repo, pr_number, post_comment)
```

### 2.4 统一单例模式

**问题：** `get_job_store()`、`get_token_tracker()`、`get_review_metrics()` 实现方式不一致

**修复方案：** 统一使用线程安全的单例装饰器

```python
from functools import wraps
import threading

def singleton(cls):
    instances = {}
    lock = threading.Lock()
    
    @wraps(cls)
    def get_instance(*args, **kwargs):
        if cls not in instances:
            with lock:
                if cls not in instances:
                    instances[cls] = cls(*args, **kwargs)
        return instances[cls]
    
    return get_instance
```

### 2.5 添加基础单元测试

**新增文件：**
- `tests/test_job_store.py` - 存储后端 CRUD 测试
- `tests/test_agent.py` - JSON 解析和上下文构建测试
- `tests/test_webhook.py` - 签名验证逻辑测试
- `tests/conftest.py` - 测试配置和 fixtures

---

## 3. 异步任务队列改造

### 3.1 架构变更

**当前架构：**
```
API请求 → BackgroundTasks → 同步执行 review_pr()
```

**目标架构：**
```
API请求 → 任务队列(Redis) → Worker池 → 并行执行 review_pr()
                  ↓
            任务状态更新 → Job Store
```

### 3.2 技术选型

**推荐使用 arq**（async Redis queue）

| 方案 | 优点 | 缺点 |
|------|------|------|
| Celery + Redis | 成熟稳定，功能丰富 | 依赖重，配置复杂 |
| 自研轻量队列 | 轻量可控 | 需要自己实现重试/监控 |
| **arq** | 原生async，轻量，Redis原生 | 社区较小 |

**选择理由：**
- 原生异步，与 FastAPI/uvicorn 契合
- 基于 Redis，与存储后端统一
- 轻量级，无额外依赖负担
- 支持重试、超时、任务过期

### 3.3 核心组件

**任务定义 (`backend/tasks.py`)：**

```python
from arq import create_pool
from arq.connections import RedisSettings

async def review_task(ctx, job_id: str, repo: str, pr_number: int, post_comment: bool):
    """异步审查任务"""
    agent = get_agent()
    try:
        result = await asyncio.to_thread(agent.review_pr, repo, pr_number, post_comment)
        job_store.update(job_id, {"status": "done", "result": result})
    except Exception as e:
        job_store.update(job_id, {"status": "error", "error": str(e)})
        raise  # 触发 arq 重试机制

class WorkerSettings:
    functions = [review_task]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = 10  # 并发任务数
    job_timeout = 300  # 5分钟超时
    max_tries = 3  # 最多重试3次
```

**API 端点改造 (`server.py`)：**

```python
@app.post("/api/review", response_model=JobStatus)
async def start_review(req: ReviewRequest, request: Request):
    job_id = generate_job_id(req.repo, req.pr_number)
    job_store.create(job_id, {...})
    
    # 入队而非直接执行
    queue = await create_pool()
    await queue.enqueue_job("review_task", job_id, req.repo, req.pr_number, req.post_comment)
    
    return job_data
```

### 3.4 任务状态追踪

**任务生命周期：**
```
pending → queued → running → done
                  ↓
                error → retry (最多3次) → failed
```

**新增状态字段：**
- `queued_at` - 入队时间
- `started_at` - 开始执行时间
- `completed_at` - 完成时间
- `retry_count` - 已重试次数
- `worker_id` - 执行任务的 worker 标识

---

## 4. Redis 存储后端增强

### 4.1 数据模型设计

**Job 存储结构：**
```
Hash: pr_review:job:{job_id}
  - job_id, status, repo, pr_number, created_at, result (JSON), error, ...

Sorted Set: pr_review:jobs_index
  - score: _created_ts (用于范围查询和排序)

Set: pr_review:jobs_by_repo:{repo}
  - 成员: job_id (用于按仓库过滤)

String: pr_review:stats:{date}
  - 每日统计缓存 (JSON)
```

**Token 记录结构：**
```
Hash: pr_review:tokens:{record_id}
  - repo, pr_number, input_tokens, output_tokens, model, created_at

Sorted Set: pr_review:tokens_weekly:{week_start}
  - score: token_count (用于周统计)
```

### 4.2 RedisJobStore 改进

**当前问题：**
- `list_all()` 使用 ZRANGE 遍历所有任务，性能差
- 缺少按状态过滤的索引
- 无数据过期清理机制

**改进后的实现：**

```python
class RedisJobStore:
    def __init__(self, redis_url=None):
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self.ttl = 7 * 24 * 3600  # 7天过期
    
    async def create(self, job_id: str, data: dict) -> None:
        pipe = self.redis.pipeline()
        key = f"pr_review:job:{job_id}"
        pipe.hset(key, mapping=data)
        pipe.expire(key, self.ttl)
        pipe.zadd("pr_review:jobs_index", {job_id: data["_created_ts"]})
        pipe.sadd(f"pr_review:status:{data['status']}", job_id)
        pipe.sadd(f"pr_review:repo:{data['repo']}", job_id)
        await pipe.execute()
    
    async def update_status(self, job_id: str, old_status: str, new_status: str, updates: dict):
        """原子化状态更新"""
        pipe = self.redis.pipeline()
        pipe.hset(f"pr_review:job:{job_id}", mapping=updates)
        pipe.srem(f"pr_review:status:{old_status}", job_id)
        pipe.sadd(f"pr_review:status:{new_status}", job_id)
        await pipe.execute()
    
    async def list_by_status(self, status: str) -> list:
        """使用索引快速查询"""
        job_ids = await self.redis.smembers(f"pr_review:status:{status}")
        return await self._fetch_jobs(job_ids)
```

### 4.3 降级策略

当 Redis 不可用时，自动降级到 FileJobStore：

```python
def get_job_store() -> InMemoryJobStore:
    global _store
    if _store:
        return _store
    
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            _store = RedisJobStore(redis_url)
            _store.redis.ping()  # 验证连接
            return _store
        except Exception as e:
            logger.warning(f"Redis unavailable, falling back to file store: {e}")
    
    _store = FileJobStore()
    return _store
```

### 4.4 数据迁移工具

**新增文件：** `scripts/migrate_to_redis.py`

```python
async def migrate_jobs(jsonl_path: str, redis_url: str):
    """迁移 FileJobStore 数据到 Redis"""
    store = RedisJobStore(redis_url)
    with open(jsonl_path) as f:
        for line in f:
            job = json.loads(line)
            await store.create(job["job_id"], job)
    print(f"Migrated jobs successfully")
```

---

## 5. 请求去重与结果缓存

### 5.1 请求去重机制

**场景：** Webhook 可能因网络重试发送重复事件；用户可能多次点击提交

**实现方案：**

```python
class ReviewDeduplicator:
    """防止重复审查请求"""
    
    def __init__(self, redis):
        self.redis = redis
        self.lock_ttl = 600  # 10分钟锁
    
    async def check_and_lock(self, repo: str, pr_number: int) -> tuple[bool, str]:
        dedup_key = f"pr_review:dedup:{repo}#{pr_number}"
        existing_job = await self.redis.get(dedup_key)
        
        if existing_job:
            status = await self.redis.hget(f"pr_review:job:{existing_job}", "status")
            if status in ("pending", "queued", "running"):
                return True, existing_job
        
        return False, None
    
    async def acquire_lock(self, repo: str, pr_number: int, job_id: str):
        dedup_key = f"pr_review:dedup:{repo}#{pr_number}"
        await self.redis.setex(dedup_key, self.lock_ttl, job_id)
    
    async def release_lock(self, repo: str, pr_number: int):
        dedup_key = f"pr_review:dedup:{repo}#{pr_number}"
        await self.redis.delete(dedup_key)
```

### 5.2 结果缓存机制

**策略：** 对已完成的审查结果进行短期缓存，避免同一 PR 短时间内重复消耗 token

```python
class ReviewCache:
    """审查结果缓存"""
    
    def __init__(self, redis):
        self.redis = redis
        self.cache_ttl = 3600  # 1小时缓存
    
    def _cache_key(self, repo: str, pr_number: int, head_sha: str) -> str:
        return f"pr_review:cache:{repo}#{pr_number}:{head_sha[:8]}"
    
    async def get_cached(self, repo: str, pr_number: int, head_sha: str) -> dict | None:
        key = self._cache_key(repo, pr_number, head_sha)
        cached = await self.redis.get(key)
        return json.loads(cached) if cached else None
    
    async def set_cached(self, repo: str, pr_number: int, head_sha: str, result: dict):
        key = self._cache_key(repo, pr_number, head_sha)
        await self.redis.setex(key, self.cache_ttl, json.dumps(result))
```

### 5.3 缓存失效策略

| 事件 | 处理方式 |
|------|----------|
| PR 有新 push (head_sha 变化) | 自动失效（基于 SHA 的缓存 key） |
| 缓存 TTL 过期 | 1 小时后自动失效 |
| 手动强制重新审查 | 提供 `force_refresh` 参数绕过缓存 |

---

## 6. 结构化日志与监控

### 6.1 结构化日志

**当前问题：** 日志是纯文本格式，难以被日志聚合系统解析

**改进方案：** 使用 JSON 格式日志

```python
import structlog

def configure_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

**日志输出示例：**
```json
{
  "event": "review_completed",
  "level": "info",
  "timestamp": "2026-06-01T10:30:00Z",
  "job_id": "job_20260601_103000_42_a1b2c3d4",
  "repo": "owner/repo",
  "pr_number": 42,
  "score": 85,
  "tokens_used": 45000,
  "duration_ms": 12500
}
```

### 6.2 Prometheus 指标暴露

**新增端点：** `GET /metrics`（Prometheus 格式）

```python
from prometheus_client import Counter, Histogram, Gauge, generate_latest

REVIEWS_TOTAL = Counter('pr_reviews_total', 'Total reviews processed', ['status', 'repo'])
REVIEW_DURATION = Histogram('review_duration_seconds', 'Review processing time', ['repo'])
ACTIVE_REVIEWS = Gauge('active_reviews', 'Currently processing reviews')
TOKEN_USAGE = Counter('tokens_used_total', 'Total tokens consumed', ['type'])
QUEUE_SIZE = Gauge('review_queue_size', 'Pending reviews in queue')
```

**关键指标：**

| 指标名称 | 类型 | 说明 |
|----------|------|------|
| `pr_reviews_total` | Counter | 审查总数（按状态分） |
| `review_duration_seconds` | Histogram | 审查耗时分布 |
| `active_reviews` | Gauge | 当前进行中的审查 |
| `tokens_used_total` | Counter | Token 消耗总量 |
| `review_queue_size` | Gauge | 队列等待数 |
| `review_errors_total` | Counter | 错误总数（按类型分） |

### 6.3 健康检查增强

**当前：** 仅返回 `{"status": "ok"}`

**增强为：**

```python
@app.get("/api/health")
async def health_check():
    checks = {
        "api": "ok",
        "redis": await check_redis(),
        "github_api": await check_github(),
        "anthropic_api": await check_anthropic(),
        "queue": await check_queue_health(),
    }
    
    all_ok = all(v == "ok" for v in checks.values())
    
    return {
        "status": "healthy" if all_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.1.0",
        "checks": checks
    }

@app.get("/api/health/ready")
async def readiness():
    """Kubernetes readiness probe"""
    queue_size = await get_queue_size()
    if queue_size > MAX_QUEUE_SIZE:
        raise HTTPException(503, "Queue full")
    return {"ready": True}

@app.get("/api/health/live")
async def liveness():
    """Kubernetes liveness probe"""
    return {"alive": True}
```

---

## 7. 生产级特性

### 7.1 优雅关闭

**场景：** 部署新版本时，需要等待正在运行的任务完成后再关闭

```python
class GracefulShutdown:
    def __init__(self):
        self.shutting_down = False
        self.active_tasks = set()
    
    def register_signals(self):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutting_down = True
    
    async def wait_for_completion(self, timeout=300):
        start = time.time()
        while self.active_tasks and time.time() - start < timeout:
            logger.info(f"Waiting for {len(self.active_tasks)} active tasks...")
            await asyncio.sleep(1)
        
        if self.active_tasks:
            logger.warning(f"Timeout reached with {len(self.active_tasks)} tasks still running")
```

### 7.2 API 限流优化

**分层限流策略：**

```python
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120/minute"],
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)

@app.post("/api/review")
@limiter.limit("10/minute")  # 提交审查：10次/分钟
async def start_review(...):
    pass

@app.get("/api/jobs/{job_id}")
@limiter.limit("60/minute")  # 查询状态：60次/分钟
async def get_job(...):
    pass
```

### 7.3 请求大小限制

```python
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_size: int = 1024 * 1024):  # 1MB
        super().__init__(app)
        self.max_size = max_size
    
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            return JSONResponse(status_code=413, content={"detail": "Request too large"})
        return await call_next(request)
```

### 7.4 配置验证

```python
from pydantic import BaseSettings, validator

class Settings(BaseSettings):
    anthropic_api_key: str
    github_token: str
    redis_url: str = "redis://localhost:6379/0"
    webhook_secret: str = ""
    api_key: str = ""
    
    @validator("github_token")
    def validate_github_token(cls, v):
        if not v.startswith(("ghp_", "github_pat_")):
            raise ValueError("Invalid GitHub token format")
        return v
    
    class Config:
        env_file = ".env"
```

### 7.5 错误追踪集成（可选）

```python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

if os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.1,
        environment=os.getenv("ENVIRONMENT", "production"),
    )
```

---

## 8. 依赖变更

### 8.1 新增依赖

```
# requirements.txt 新增
arq>=0.25.0
structlog>=23.1.0
prometheus-client>=0.17.0
sentry-sdk[fastapi]>=1.28.0  # 可选
```

### 8.2 环境变量

```
# 新增环境变量
REDIS_URL=redis://localhost:6379/0  # 默认使用 Redis
SENTRY_DSN=                          # 可选，错误追踪
ENVIRONMENT=production               # 环境标识
MAX_QUEUE_SIZE=100                   # 队列容量限制
WORKER_CONCURRENCY=10                # Worker 并发数
```

---

## 9. 实施计划

### 阶段 1：Bug 修复与基础加固（1-2 天）
- [ ] 修复 datetime 弃用警告
- [ ] 修复前端轮询内存泄漏
- [ ] 改进 asyncio 事件循环处理
- [ ] 统一单例模式
- [ ] 添加基础单元测试

### 阶段 2：异步任务队列（3-5 天）
- [ ] 集成 arq 任务队列
- [ ] 实现 Worker 进程
- [ ] 改造 API 端点
- [ ] 实现任务状态追踪

### 阶段 3：Redis 存储与缓存（2-3 天）
- [ ] 增强 RedisJobStore
- [ ] 实现请求去重
- [ ] 实现结果缓存
- [ ] 编写数据迁移工具

### 阶段 4：监控与生产特性（2-3 天）
- [ ] 集成结构化日志
- [ ] 添加 Prometheus 指标
- [ ] 增强健康检查
- [ ] 实现优雅关闭
- [ ] 优化 API 限流

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Redis 服务中断 | 任务队列不可用 | 实现自动降级到 FileJobStore |
| arq 库不成熟 | 可能遇到未知问题 | 准备回退方案，保持 BackgroundTasks 作为备选 |
| 数据迁移失败 | 历史数据丢失 | 提供回滚脚本，先备份再迁移 |
| 性能回退 | 用户体验下降 | 分阶段部署，每阶段进行性能测试 |

---

## 11. 成功标准

- [ ] 所有已知 Bug 修复，单元测试覆盖率 > 60%
- [ ] 支持 10+ 并发审查任务
- [ ] API 响应时间 P95 < 200ms（不含审查时间）
- [ ] 任务队列支持自动重试，失败率 < 1%
- [ ] 健康检查端点可用性 99.9%
- [ ] 结构化日志可被 ELK/Loki 正确解析
- [ ] Prometheus 指标可被 Grafana 正确展示

---

## 附录 A：文件变更清单

### 新增文件
- `backend/tasks.py` - arq 任务定义
- `backend/dedup.py` - 请求去重逻辑
- `backend/cache.py` - 结果缓存逻辑
- `backend/shutdown.py` - 优雅关闭逻辑
- `backend/settings.py` - Pydantic 配置验证
- `scripts/migrate_to_redis.py` - 数据迁移工具
- `scripts/start_worker.py` - Worker 启动脚本
- `tests/test_job_store.py` - 存储后端测试
- `tests/test_agent.py` - 代理逻辑测试
- `tests/test_webhook.py` - Webhook 测试
- `tests/conftest.py` - 测试配置

### 修改文件
- `backend/server.py` - API 端点改造
- `backend/job_store.py` - 修复 datetime，增强 RedisJobStore
- `backend/token_tracker.py` - 修复 datetime
- `backend/webhook.py` - 集成去重逻辑
- `backend/logging_config.py` - 集成 structlog
- `backend/rate_limit.py` - 优化限流配置
- `frontend/index.html` - 修复轮询内存泄漏
- `requirements.txt` - 新增依赖
- `config/.env.example` - 新增环境变量

---

*文档生成时间：2026-06-01*
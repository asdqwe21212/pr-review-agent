"""arq 任务定义"""
import os
import asyncio
import logging
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker

from backend.agent import PRReviewAgent
from backend.job_store import get_job_store

logger = logging.getLogger(__name__)


async def review_task(ctx, job_id: str, repo: str, pr_number: int, post_comment: bool):
    """异步审查任务"""
    logger.info(f"Starting review task for {repo}#{pr_number} (job: {job_id})")
    
    job_store = get_job_store()
    job_store.update(job_id, {"status": "running", "started_at": ctx.get("started_at")})
    
    try:
        github_token = os.getenv("GITHUB_TOKEN")
        
        if not github_token:
            raise ValueError("Missing GITHUB_TOKEN")
        
        agent = PRReviewAgent(github_token)
        
        result = await asyncio.to_thread(
            agent.review_pr, repo, pr_number, post_comment
        )
        
        job_store.update(job_id, {
            "status": "done",
            "result": result,
            "completed_at": ctx.get("finished_at"),
        })
        logger.info(f"Review completed for {repo}#{pr_number}, score: {result['review'].get('overall_score', 'N/A')}")
        
        return {"job_id": job_id, "score": result['review'].get('overall_score')}
        
    except Exception as e:
        logger.error(f"Review failed for {repo}#{pr_number}: {e}", exc_info=True)
        job_store.update(job_id, {
            "status": "error",
            "error": str(e),
            "completed_at": ctx.get("finished_at"),
        })
        raise


class WorkerSettings:
    """arq Worker 配置"""
    functions = [review_task]
    redis_settings = RedisSettings.from_dsn(
        os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    max_jobs = int(os.getenv("WORKER_CONCURRENCY", "10"))
    job_timeout = 300
    max_tries = 3
    keep_result = 3600
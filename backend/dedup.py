"""请求去重逻辑"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class ReviewDeduplicator:
    """防止重复审查请求"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
        self.lock_ttl = 600  # 10分钟锁
    
    def _dedup_key(self, repo: str, pr_number: int) -> str:
        return f"pr_review:dedup:{repo}#{pr_number}"
    
    def check_and_lock(self, repo: str, pr_number: int) -> Tuple[bool, Optional[str]]:
        """
        检查是否正在审查，如果没有则加锁
        返回: (is_duplicate, existing_job_id)
        """
        dedup_key = self._dedup_key(repo, pr_number)
        
        existing_job = self.redis.get(dedup_key)
        if existing_job:
            status = self.redis.hget(f"pr_review:job:{existing_job}", "status")
            if status in ("pending", "queued", "running"):
                logger.info(f"Duplicate request detected for {repo}#{pr_number}, existing job: {existing_job}")
                return True, existing_job
        
        return False, None
    
    def acquire_lock(self, repo: str, pr_number: int, job_id: str):
        """获取审查锁"""
        dedup_key = self._dedup_key(repo, pr_number)
        self.redis.setex(dedup_key, self.lock_ttl, job_id)
        logger.debug(f"Lock acquired for {repo}#{pr_number}, job: {job_id}")
    
    def release_lock(self, repo: str, pr_number: int):
        """释放审查锁"""
        dedup_key = self._dedup_key(repo, pr_number)
        self.redis.delete(dedup_key)
        logger.debug(f"Lock released for {repo}#{pr_number}")
"""结果缓存逻辑"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ReviewCache:
    """审查结果缓存"""
    
    def __init__(self, redis_client, ttl: int = 3600):
        self.redis = redis_client
        self.cache_ttl = ttl  # 默认1小时
    
    def _cache_key(self, repo: str, pr_number: int, head_sha: str) -> str:
        """生成缓存 key"""
        return f"pr_review:cache:{repo}#{pr_number}:{head_sha[:8]}"
    
    def get_cached(self, repo: str, pr_number: int, head_sha: str) -> Optional[dict]:
        """获取缓存的审查结果"""
        key = self._cache_key(repo, pr_number, head_sha)
        cached = self.redis.get(key)
        
        if cached:
            logger.info(f"Cache hit for {repo}#{pr_number} (sha: {head_sha[:8]})")
            return json.loads(cached)
        
        logger.debug(f"Cache miss for {repo}#{pr_number}")
        return None
    
    def set_cached(self, repo: str, pr_number: int, head_sha: str, result: dict):
        """缓存审查结果"""
        key = self._cache_key(repo, pr_number, head_sha)
        self.redis.setex(key, self.cache_ttl, json.dumps(result))
        logger.info(f"Cached result for {repo}#{pr_number} (sha: {head_sha[:8]})")
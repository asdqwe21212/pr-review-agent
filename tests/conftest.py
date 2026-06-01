"""测试配置和 fixtures"""
import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


@pytest.fixture
def temp_dir():
    """创建临时目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_redis():
    """模拟 Redis 客户端"""
    redis = MagicMock()
    redis.ping.return_value = True
    redis.get.return_value = None
    redis.set.return_value = True
    redis.hget.return_value = None
    redis.hset.return_value = True
    return redis


@pytest.fixture
def sample_job_data():
    """示例任务数据"""
    return {
        "job_id": "test_job_001",
        "status": "pending",
        "repo": "owner/repo",
        "pr_number": 42,
        "created_at": "2026-01-01T00:00:00Z",
        "_created_ts": 1735689600.0,
    }
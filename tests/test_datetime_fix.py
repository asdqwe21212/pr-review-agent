"""测试 datetime 不使用已弃用的 utcnow()"""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


def test_file_job_store_uses_timezone_aware_datetime():
    """FileJobStore 应使用 datetime.now(timezone.utc) 而非 datetime.utcnow()"""
    from backend.job_store import FileJobStore
    
    # 验证导入中包含 timezone
    import backend.job_store as js_module
    source = open(js_module.__file__).read()
    assert "timezone" in source
    assert "utcnow()" not in source


def test_token_tracker_week_start_uses_utc():
    """TokenTracker._week_start 应使用 datetime.now(timezone.utc)"""
    from backend.token_tracker import TokenTracker
    
    tracker = TokenTracker.__new__(TokenTracker)
    result = tracker._week_start()
    
    assert result.tzinfo is not None
    assert result.tzinfo == timezone.utc
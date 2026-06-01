"""Job Store 测试"""
import pytest
from unittest.mock import patch, MagicMock
from backend.job_store import InMemoryJobStore


class TestInMemoryJobStore:
    def test_create_and_get(self, sample_job_data):
        """创建并获取任务"""
        store = InMemoryJobStore()
        store.create(sample_job_data["job_id"], sample_job_data)
        
        result = store.get(sample_job_data["job_id"])
        assert result is not None
        assert result["job_id"] == sample_job_data["job_id"]
    
    def test_update(self, sample_job_data):
        """更新任务状态"""
        store = InMemoryJobStore()
        store.create(sample_job_data["job_id"], sample_job_data)
        
        store.update(sample_job_data["job_id"], {"status": "running"})
        result = store.get(sample_job_data["job_id"])
        assert result["status"] == "running"
    
    def test_list_all(self, sample_job_data):
        """列出所有任务"""
        store = InMemoryJobStore()
        store.create(sample_job_data["job_id"], sample_job_data)
        
        jobs = store.list_all()
        assert len(jobs) == 1
    
    def test_list_by_status(self, sample_job_data):
        """按状态列出任务"""
        store = InMemoryJobStore()
        store.create(sample_job_data["job_id"], sample_job_data)
        
        pending = store.list_by_status("pending")
        assert len(pending) == 1
        
        done = store.list_by_status("done")
        assert len(done) == 0
"""Worker 启动脚本"""
import sys
import os
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from arq import run_worker
from backend.tasks import WorkerSettings


def main():
    """启动 Worker"""
    print("Starting PR Review Agent Worker...")
    print(f"Redis URL: {os.getenv('REDIS_URL', 'redis://localhost:6379/0')}")
    print(f"Concurrency: {os.getenv('WORKER_CONCURRENCY', '10')}")
    
    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
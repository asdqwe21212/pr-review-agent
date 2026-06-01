"""优雅关闭逻辑"""
import signal
import asyncio
import logging
from typing import Set

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """优雅关闭管理器"""
    
    def __init__(self):
        self.shutting_down = False
        self.active_tasks: Set[str] = set()
        self._shutdown_event = asyncio.Event()
    
    def register_signals(self):
        """注册信号处理器"""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        logger.info("Shutdown signal handlers registered")
    
    def _handle_signal(self, signum, frame):
        """处理关闭信号"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutting_down = True
        self._shutdown_event.set()
    
    def add_task(self, task_id: str):
        """添加活跃任务"""
        self.active_tasks.add(task_id)
        logger.debug(f"Task added: {task_id}, active: {len(self.active_tasks)}")
    
    def remove_task(self, task_id: str):
        """移除活跃任务"""
        self.active_tasks.discard(task_id)
        logger.debug(f"Task removed: {task_id}, active: {len(self.active_tasks)}")
    
    async def wait_for_completion(self, timeout: int = 300):
        """等待活跃任务完成"""
        if not self.active_tasks:
            logger.info("No active tasks, shutting down immediately")
            return
        
        logger.info(f"Waiting for {len(self.active_tasks)} active tasks (timeout: {timeout}s)")
        start = asyncio.get_event_loop().time()
        
        while self.active_tasks:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= timeout:
                logger.warning(f"Shutdown timeout reached with {len(self.active_tasks)} tasks still running")
                break
            
            logger.info(f"Waiting for {len(self.active_tasks)} active tasks... ({int(elapsed)}s elapsed)")
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        
        logger.info("Graceful shutdown complete")


# 全局实例
shutdown_handler = GracefulShutdown()
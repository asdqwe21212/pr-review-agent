"""线程安全的单例装饰器"""
from functools import wraps
import threading
from typing import TypeVar, Type, Callable

T = TypeVar('T')

def singleton(cls: Type[T]) -> Callable[..., T]:
    """将类转换为单例"""
    instances = {}
    lock = threading.Lock()
    
    @wraps(cls)
    def get_instance(*args, **kwargs) -> T:
        if cls not in instances:
            with lock:
                if cls not in instances:
                    instances[cls] = cls(*args, **kwargs)
        return instances[cls]
    
    return get_instance
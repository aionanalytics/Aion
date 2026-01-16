"""
Caching utilities for performance optimization.
"""

from __future__ import annotations

import time
from functools import wraps, lru_cache
from typing import Any, Callable, Dict, Optional, Tuple


def timed_lru_cache(seconds: int, maxsize: int = 128):
    """
    LRU cache that expires after seconds duration.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            time_bucket = int(time.time() // seconds)
            cache_key = (time_bucket, args, tuple(sorted(kwargs.items())))
            return _cached_call(cache_key)
        
        @lru_cache(maxsize=maxsize)
        def _cached_call(cache_key: Tuple) -> Any:
            _, args, kwargs_tuple = cache_key
            kwargs = dict(kwargs_tuple)
            return func(*args, **kwargs)
        
        wrapper.cache_clear = _cached_call.cache_clear
        return wrapper
    
    return decorator

import time
from django.core.cache import cache
from django.db import transaction

CACHE_VERSION_KEY = 'arohan:cache_version'


def get_cache_version() -> int:
    """Returns the current global data cache version, initializing if absent."""
    ver = cache.get(CACHE_VERSION_KEY)
    if ver is None:
        ver = int(time.time() * 1000)
        cache.add(CACHE_VERSION_KEY, ver, timeout=None)
        ver = cache.get(CACHE_VERSION_KEY, ver)
    return ver


def bump_cache_version() -> None:
    """Atomically invalidates all versioned API/report caches."""
    try:
        cache.incr(CACHE_VERSION_KEY)
    except ValueError:
        cache.set(CACHE_VERSION_KEY, int(time.time() * 1000), timeout=None)


def invalidate_on_commit() -> None:
    """Schedules cache invalidation after the active DB transaction commits."""
    transaction.on_commit(bump_cache_version)


def make_cache_key(*parts) -> str:
    ver = get_cache_version()
    joined = ':'.join(str(p) for p in parts)
    return f"arohan:v{ver}:{joined}"

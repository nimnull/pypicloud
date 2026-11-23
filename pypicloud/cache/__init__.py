"""Caching database implementations"""

from pypicloud.util import register_backend, resolve_backend

from .base import ICache
from .redis_cache import RedisCache
from .sql import SQLCache

__all__ = ["ICache", "RedisCache", "SQLCache", "get_cache_impl", "includeme"]


# boto3 is a core requirement, but the dynamo module is only imported on demand
_BACKENDS: dict[str, str | type] = {
    "sql": "pypicloud.cache.SQLCache",
    "redis": "pypicloud.cache.RedisCache",
    "dynamo": "pypicloud.cache.dynamo.DynamoCache",
}


def get_cache_impl(settings):
    """Get the cache class from settings"""
    return resolve_backend(settings.get("pypi.db", "sql"), _BACKENDS, __name__)


def includeme(config):
    """Get and configure the cache db wrapper"""
    settings = config.get_settings()
    cache_impl = get_cache_impl(settings)
    kwargs = cache_impl.configure(settings)
    cache_impl(**kwargs).reload_if_needed()
    register_backend(config, cache_impl, kwargs, "db")
    return cache_impl

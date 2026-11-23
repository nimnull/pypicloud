"""Classes that provide user and package permissions"""

from pypicloud.util import register_backend, resolve_backend

from .aws_secrets_manager import AWSSecretsManagerAccessBackend
from .base import (
    SCHEMES,
    IAccessBackend,
    IMutableAccessBackend,
    Permission,
    get_pwd_context,
)
from .config import ConfigAccessBackend
from .remote import RemoteAccessBackend
from .sql import SQLAccessBackend

__all__ = [
    "SCHEMES",
    "AWSSecretsManagerAccessBackend",
    "ConfigAccessBackend",
    "IAccessBackend",
    "IMutableAccessBackend",
    "Permission",
    "RemoteAccessBackend",
    "SQLAccessBackend",
    "get_pwd_context",
    "includeme",
]


# python-ldap is an optional extra, so the LDAP backend is named, not imported
_BACKENDS: dict[str, str | type] = {
    "config": ConfigAccessBackend,
    "remote": RemoteAccessBackend,
    "sql": SQLAccessBackend,
    "ldap": "pypicloud.access.ldap_.LDAPAccessBackend",
    "aws_secrets_manager": AWSSecretsManagerAccessBackend,
}


def includeme(config) -> None:
    """Configure the app"""
    settings = config.get_settings()
    backend = resolve_backend(settings.get("pypi.auth", "config"), _BACKENDS, __name__)
    register_backend(config, backend, backend.configure(settings), "access")

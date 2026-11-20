"""Classes that provide user and package permissions"""

from functools import partial

from pyramid.path import DottedNameResolver

from .aws_secrets_manager import AWSSecretsManagerAccessBackend
from .base import SCHEMES, IAccessBackend, IMutableAccessBackend, get_pwd_context
from .config import ConfigAccessBackend
from .remote import RemoteAccessBackend
from .sql import SQLAccessBackend

__all__ = [
    "SCHEMES",
    "AWSSecretsManagerAccessBackend",
    "ConfigAccessBackend",
    "IAccessBackend",
    "IMutableAccessBackend",
    "RemoteAccessBackend",
    "SQLAccessBackend",
    "get_pwd_context",
    "includeme",
]


def includeme(config) -> None:
    """Configure the app"""
    settings = config.get_settings()

    resolver = DottedNameResolver(__name__)
    dotted_name: str | type[IAccessBackend]
    match settings.get("pypi.auth", "config"):
        case "config":
            dotted_name = ConfigAccessBackend
        case "remote":
            dotted_name = RemoteAccessBackend
        case "sql":
            dotted_name = SQLAccessBackend
        case "ldap":
            dotted_name = "pypicloud.access.ldap_.LDAPAccessBackend"
        case "aws_secrets_manager":
            dotted_name = AWSSecretsManagerAccessBackend
        case other:
            dotted_name = other
    access_backend = resolver.maybe_resolve(dotted_name)
    kwargs = access_backend.configure(settings)
    config.add_request_method(
        partial(access_backend, **kwargs), name="access", reify=True
    )
    config.add_postfork_hook(partial(access_backend.postfork, **kwargs))

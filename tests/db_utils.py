import os
import unittest
from typing import Any

from botocore.exceptions import BotoCoreError


def configure_dynamo_cache(**extra_settings: Any) -> dict[str, Any]:
    """Configure a DynamoCache against the DynamoDB Local service.

    Reads DYNAMO_HOST and DYNAMO_PORT, defaulting to localhost:8000, and raises
    unittest.SkipTest when no service answers there.
    """
    from pypicloud.cache.dynamo import DynamoCache

    host = os.environ.get("DYNAMO_HOST", "localhost")
    port = os.environ.get("DYNAMO_PORT", "8000")
    settings = {
        "pypi.storage": "tests.DummyStorage",
        "db.region_name": "us-east-1",
        "db.host": host,
        "db.port": port,
        "db.namespace": "test.",
        # DynamoDB Local ignores the values but rejects empty credentials
        "db.aws_access_key_id": "dummy",
        "db.aws_secret_access_key": "dummy",
        **extra_settings,
    }
    try:
        return DynamoCache.configure(settings)
    except BotoCoreError as e:
        raise unittest.SkipTest(f"Couldn't connect to DynamoDB at {host}:{port}") from e


def get_sqlite_url() -> str:
    return "sqlite://"


def get_mysql_url() -> str:
    user = os.environ.get("MYSQL_USER", "root")
    pw = os.environ.get("MYSQL_PASSWORD")
    pw = "" if pw is None else ":" + pw
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    port = os.environ.get("MYSQL_PORT", "3306")
    db = os.environ.get("MYSQL_DB", "test")
    return f"mysql://{user}{pw}@{host}:{port}/{db}?charset=utf8mb4"


def get_postgres_url() -> str:
    user = os.environ.get("POSTGRES_USER", "postgres")
    pw = os.environ.get("POSTGRES_PASSWORD")
    pw = "" if pw is None else ":" + pw
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "postgres")
    return f"postgresql://{user}{pw}@{host}:{port}/{db}"


def get_gcs_url() -> str:
    """URL of the fake-gcs-server API, from GCS_HOST and GCS_PORT."""
    host = os.environ.get("GCS_HOST", "localhost")
    port = os.environ.get("GCS_PORT", "4443")
    return f"http://{host}:{port}"

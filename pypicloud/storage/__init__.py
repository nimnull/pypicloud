"""Storage backend implementations"""

from collections.abc import Callable
from functools import partial
from typing import Any

from pypicloud.util import resolve_backend

from .base import IStorage
from .files import FileStorage
from .s3 import CloudFrontS3Storage, S3Storage

try:
    from .gcs import GoogleCloudStorage

    GCS_IS_AVAILABLE = True
except ImportError:
    GCS_IS_AVAILABLE = False

try:
    from .azure_blob import AzureBlobStorage

    AZURE_BLOB_IS_AVAILABLE = True
except ImportError:
    AZURE_BLOB_IS_AVAILABLE = False

__all__ = [
    "AZURE_BLOB_IS_AVAILABLE",
    "GCS_IS_AVAILABLE",
    "AzureBlobStorage",
    "CloudFrontS3Storage",
    "FileStorage",
    "GoogleCloudStorage",
    "IStorage",
    "S3Storage",
    "get_storage_impl",
]


_BACKENDS: dict[str, str | type] = {
    "azure-blob": "pypicloud.storage.AzureBlobStorage",
    "s3": "pypicloud.storage.S3Storage",
    "cloudfront": "pypicloud.storage.CloudFrontS3Storage",
    "gcs": "pypicloud.storage.GoogleCloudStorage",
    "file": "pypicloud.storage.FileStorage",
}


def get_storage_impl(settings) -> Callable[[Any], Any]:
    """Get and configure the storage backend wrapper"""
    name = settings.get("pypi.storage", "file")
    if name == "azure-blob" and not AZURE_BLOB_IS_AVAILABLE:
        raise ValueError(
            "azure-blob storage backend selected but Azure Blob "
            "Storage is not available. "
            "Please install the azure-storage-blob library by "
            "including the `azure-blob` extra in your pip-install step. "
            "For example: `pip install pypicloud[azure-blob]`"
        )
    if name == "gcs" and not GCS_IS_AVAILABLE:
        raise ValueError(
            "gcs backend selected but GCS is not available. "
            "Please install the google-cloud-storage library by "
            "including the `gcs` extra in your pip-install step. "
            "For example: `pip install pypicloud[gcs]`"
        )
    storage_impl = resolve_backend(name, _BACKENDS, __name__)
    return partial(storage_impl, **storage_impl.configure(settings))

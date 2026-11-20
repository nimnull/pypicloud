"""Store package data in DynamoDB"""

import logging
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from itertools import batched
from typing import Any, override

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from pyramid.settings import asbool, aslist

from pypicloud.models import Package

from .base import ICache

LOG = logging.getLogger(__name__)

PACKAGES_TABLE = "DynamoPackage"
SUMMARIES_TABLE = "PackageSummary"
NAME_INDEX = "name-index"
DEFAULT_THROUGHPUT = {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}
# DynamoDB accepts at most 100 keys per BatchGetItem request
BATCH_GET_SIZE = 100


class DynamoPackage(Package):
    """Python package stored in DynamoDB"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # DynamoDB doesn't play nice with empty strings.
        if not self.summary:
            self.summary = None

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> DynamoPackage:
        """Build a package from a DynamoDB item"""
        return cls(
            item["name"],
            item["version"],
            item["filename"],
            _to_datetime(item["last_modified"]),
            item.get("summary"),
            **item.get("data", {}),
        )

    def to_item(self) -> dict[str, Any]:
        """The DynamoDB item for this package"""
        return {
            "filename": self.filename,
            "name": self.name,
            "version": self.version,
            "last_modified": _to_timestamp(self.last_modified),
            "summary": self.summary,
            "data": self.data,
        }


class PackageSummary:
    """Aggregate data about all versions of one package"""

    def __init__(self, package: Package):
        self.name = package.name
        self.summary = package.summary
        self.last_modified = package.last_modified.replace(tzinfo=UTC)

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> PackageSummary:
        """Build a summary from a DynamoDB item"""
        summary = cls.__new__(cls)
        summary.name = item["name"]
        summary.summary = item.get("summary")
        summary.last_modified = _to_datetime(item["last_modified"])
        return summary

    def to_item(self) -> dict[str, Any]:
        """The DynamoDB item for this summary"""
        return {
            "name": self.name,
            "summary": self.summary,
            "last_modified": _to_timestamp(self.last_modified),
        }

    def __json__(self, request=None) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "last_modified": self.last_modified,
        }


def _to_timestamp(value: datetime) -> Decimal:
    """Seconds since the epoch, as DynamoDB stores them"""
    return Decimal(str(value.timestamp()))


def _to_datetime(value: Decimal) -> datetime:
    """Read a stored timestamp back into an aware UTC datetime"""
    return datetime.fromtimestamp(float(value), UTC)


def _iter_items(table, **kwargs: Any) -> Iterator[dict[str, Any]]:
    """Every item of a table, one page of a scan at a time"""
    start_key = None
    while True:
        if start_key is not None:
            kwargs["ExclusiveStartKey"] = start_key
        response = table.scan(**kwargs)
        yield from response["Items"]
        start_key = response.get("LastEvaluatedKey")
        if start_key is None:
            return


def _packages_table_args(throughput: dict[str, Any]) -> dict[str, Any]:
    """create_table() arguments for the package table and its name index"""
    index_throughput = throughput.get(NAME_INDEX, DEFAULT_THROUGHPUT)
    return {
        "KeySchema": [{"AttributeName": "filename", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "filename", "AttributeType": "S"},
            {"AttributeName": "name", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": NAME_INDEX,
                "KeySchema": [{"AttributeName": "name", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": index_throughput,
            }
        ],
        "ProvisionedThroughput": _table_throughput(throughput),
    }


def _summaries_table_args(throughput: dict[str, Any]) -> dict[str, Any]:
    """create_table() arguments for the summary table"""
    return {
        "KeySchema": [{"AttributeName": "name", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "name", "AttributeType": "S"}],
        "ProvisionedThroughput": _table_throughput(throughput),
    }


def _table_throughput(throughput: dict[str, Any]) -> dict[str, int]:
    """The table's own capacity out of a throughput mapping"""
    return {
        "ReadCapacityUnits": throughput.get(
            "ReadCapacityUnits", DEFAULT_THROUGHPUT["ReadCapacityUnits"]
        ),
        "WriteCapacityUnits": throughput.get(
            "WriteCapacityUnits", DEFAULT_THROUGHPUT["WriteCapacityUnits"]
        ),
    }


def _create_table(dynamo, name: str, args: dict[str, Any]):
    """Create a table unless it already exists, and wait until it is usable"""
    table = dynamo.Table(name)
    try:
        dynamo.create_table(TableName=name, **args)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise
    else:
        table.wait_until_exists()
    return table


def _describe_throughput(table) -> dict[str, Any]:
    """The provisioned capacity of a table and of each of its global indexes"""
    description = table.meta.client.describe_table(TableName=table.name)["Table"]
    throughput = {
        key: description["ProvisionedThroughput"][key]
        for key in ("ReadCapacityUnits", "WriteCapacityUnits")
    }
    for index in description.get("GlobalSecondaryIndexes", []):
        throughput[index["IndexName"]] = {
            key: index["ProvisionedThroughput"][key]
            for key in ("ReadCapacityUnits", "WriteCapacityUnits")
        }
    return throughput


class DynamoCache(ICache):
    """Caching database that uses DynamoDB"""

    def __init__(
        self,
        request=None,
        dynamo=None,
        packages=None,
        summaries=None,
        graceful_reload=False,
        **kwargs,
    ):
        super().__init__(request, **kwargs)
        self.dynamo = dynamo
        self.packages = packages
        self.summaries = summaries
        self.graceful_reload = graceful_reload

    @override
    def new_package(self, *args, **kwargs):
        return DynamoPackage(*args, **kwargs)

    @classmethod
    @override
    def configure(cls, settings):
        kwargs = super().configure(settings)

        region = settings.get("db.region_name")
        host = settings.get("db.host")
        port = int(settings.get("db.port", 8000))
        secure = asbool(settings.get("db.secure", False))
        namespace = settings.get("db.namespace", "")
        if host is None and region is None:
            raise ValueError("Must specify either db.region_name or db.host!")

        packages_name, summaries_name = PACKAGES_TABLE, SUMMARIES_TABLE
        tablenames = aslist(settings.get("db.tablenames", []))
        if tablenames:
            if len(tablenames) != 2:
                raise ValueError("db.tablenames must be a 2-element list")
            packages_name, summaries_name = tablenames

        session = boto3.Session(
            aws_access_key_id=settings.get("db.aws_access_key_id"),
            aws_secret_access_key=settings.get("db.aws_secret_access_key"),
            region_name=region,
        )
        endpoint = None
        if host is not None:
            endpoint = f"{'https' if secure else 'http'}://{host}:{port}"
        dynamo = session.resource("dynamodb", endpoint_url=endpoint)

        LOG.info("Checking if DynamoDB tables exist")
        kwargs["dynamo"] = dynamo
        kwargs["packages"] = _create_table(
            dynamo, namespace + packages_name, _packages_table_args({})
        )
        kwargs["summaries"] = _create_table(
            dynamo, namespace + summaries_name, _summaries_table_args({})
        )
        kwargs["graceful_reload"] = asbool(settings.get("db.graceful_reload", False))
        return kwargs

    @override
    def fetch(self, filename):
        item = self.packages.get_item(Key={"filename": filename}).get("Item")
        return None if item is None else DynamoPackage.from_item(item)

    @override
    def all(self, name):
        packages = [
            DynamoPackage.from_item(item)
            for item in self._query_name(self.packages, name)
        ]
        return sorted(packages, reverse=True)

    def _query_name(self, table, name: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        """Every item of the name index for one package name, a page at a time"""
        start_key = None
        while True:
            if start_key is not None:
                kwargs["ExclusiveStartKey"] = start_key
            response = table.query(
                IndexName=NAME_INDEX,
                KeyConditionExpression=Key("name").eq(name),
                **kwargs,
            )
            yield from response["Items"]
            start_key = response.get("LastEvaluatedKey")
            if start_key is None:
                return

    @override
    def distinct(self):
        return sorted({item["name"] for item in _iter_items(self.summaries)})

    @override
    def summary(self):
        summaries = sorted(
            (PackageSummary.from_item(item) for item in _iter_items(self.summaries)),
            key=lambda s: s.name,
        )
        return [s.__json__() for s in summaries]

    @override
    def clear(self, package):
        self.packages.delete_item(Key={"filename": package.filename})
        self._maybe_delete_summary(package.name)

    def _maybe_delete_summary(self, package_name):
        """Check for any package with the name. Delete summary if 0

        Reads the name index, which is eventually consistent, so a summary may
        outlive the last of its packages until the next delete.
        """
        remaining = next(self._query_name(self.packages, package_name, Limit=1), None)
        if remaining is None:
            LOG.info("Removing package summary %s", package_name)
            self.summaries.delete_item(Key={"name": package_name})

    @override
    def clear_all(self):
        # We're replacing the tables, so make sure we save and restore the
        # current table/index throughput
        tables = {
            self.packages: _packages_table_args,
            self.summaries: _summaries_table_args,
        }
        recreate = {}
        for table, table_args in tables.items():
            recreate[table.name] = table_args(_describe_throughput(table))
            table.delete()
        for table in tables:
            table.wait_until_not_exists()
        self.packages = _create_table(
            self.dynamo, self.packages.name, recreate[self.packages.name]
        )
        self.summaries = _create_table(
            self.dynamo, self.summaries.name, recreate[self.summaries.name]
        )

    @override
    def save(self, package):
        self.packages.put_item(Item=package.to_item())
        self.summaries.put_item(Item=PackageSummary(package).to_item())

    def _save_packages(self, packages):
        """Write packages to the cache in batches"""
        with self.packages.batch_writer() as batch:
            for package in packages:
                batch.put_item(Item=package.to_item())

    def _delete_packages(self, packages):
        """Remove packages from the cache in batches"""
        with self.packages.batch_writer() as batch:
            for package in packages:
                batch.delete_item(Key={"filename": package.filename})

    def _fetch_summaries(self, names) -> list[PackageSummary]:
        """Fetch the summaries for a collection of package names"""
        summaries = []
        for chunk in batched(names, BATCH_GET_SIZE, strict=False):
            keys = [{"name": name} for name in chunk]
            while keys:
                response = self.dynamo.batch_get_item(
                    RequestItems={self.summaries.name: {"Keys": keys}}
                )
                summaries += [
                    PackageSummary.from_item(item)
                    for item in response["Responses"].get(self.summaries.name, [])
                ]
                unprocessed = response.get("UnprocessedKeys", {})
                keys = unprocessed.get(self.summaries.name, {}).get("Keys", [])
        return summaries

    @override
    def reload_from_storage(self, clear=True):
        if not self.graceful_reload:
            return super().reload_from_storage(clear)
        LOG.info("Rebuilding cache from storage")
        # Log start time
        start = datetime.now(UTC)
        # Fetch packages from storage s1
        s1 = set(self.storage.list(self.new_package))
        # Fetch cache packages c1
        c1 = {DynamoPackage.from_item(item) for item in _iter_items(self.packages)}
        # Add missing packages to cache (s1 - c1)
        missing = s1 - c1
        if missing:
            LOG.info("Adding %d missing packages to cache", len(missing))
            self._save_packages(missing)
        # Delete extra packages from cache (c1 - s1) when last_modified < start
        # The time filter helps us avoid deleting packages that were
        # concurrently uploaded.
        extra1 = [p for p in (c1 - s1) if p.last_modified < start]
        if extra1:
            LOG.info("Removing %d extra packages from cache", len(extra1))
            self._delete_packages(extra1)

        # If any packages were concurrently deleted during the cache rebuild,
        # we can detect them by polling storage again and looking for any
        # packages that were present in s1 and are missing from s2
        s2 = set(self.storage.list(self.new_package))
        # Delete extra packages from cache (s1 - s2)
        extra2 = s1 - s2
        if extra2:
            LOG.info(
                "Removing %d packages from cache that were concurrently "
                "deleted during rebuild",
                len(extra2),
            )
            self._delete_packages(extra2)
            # Remove these concurrently-deleted files from the list of packages
            # that were missing from the cache. Don't want to use those to
            # update the summaries below.
            missing -= extra2

        # Update the PackageSummary for added packages
        packages_by_name = defaultdict(list)
        for package in missing:
            # Set the tz here so we can compare against the PackageSummary
            package.last_modified = package.last_modified.replace(tzinfo=UTC)
            packages_by_name[package.name].append(package)
        summaries = self._fetch_summaries(packages_by_name)
        summaries_by_name = {summary.name: summary for summary in summaries}
        for name, packages in packages_by_name.items():
            if name in summaries_by_name:
                summary = summaries_by_name[name]
            else:
                summary = PackageSummary(packages[0])
                summaries.append(summary)
            for package in packages:
                if package.last_modified > summary.last_modified:
                    summary.last_modified = package.last_modified
                    summary.summary = package.summary
        if summaries:
            LOG.info("Updating %d package summaries", len(summaries))
            with self.summaries.batch_writer() as batch:
                for summary in summaries:
                    batch.put_item(Item=summary.to_item())

        # Remove the PackageSummary for deleted packages
        removed = set()
        for package in extra1:
            removed.add(package.name)
        for package in extra2:
            removed.add(package.name)
        for name in removed:
            self._maybe_delete_summary(name)

    @override
    def check_health(self):
        try:
            self.summaries.scan(Limit=1)
        except Exception as e:
            return False, str(e)
        else:
            return True, ""

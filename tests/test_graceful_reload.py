"""Tests for gracefully reloading the caches"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import redis
import transaction
from pyramid.testing import DummyRequest
from sqlalchemy.exc import OperationalError

from pypicloud.cache import RedisCache, SQLCache
from pypicloud.cache.dynamo import (
    BATCH_GET_SIZE,
    DynamoCache,
    DynamoPackage,
    PackageSummary,
    _iter_items,
)
from pypicloud.cache.sql import SQLPackage
from pypicloud.storage import IStorage
from pypicloud.util import EnvironSettings

from . import make_package
from .db_utils import (
    clear_dynamo_table,
    configure_dynamo_cache,
    get_mysql_url,
    get_postgres_url,
    get_sqlite_url,
    scan_packages,
)


class TestDynamoCache(unittest.TestCase):
    """Tests for the DynamoCache"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kwargs = configure_dynamo_cache(**{"db.graceful_reload": True})
        cls.packages = cls.kwargs["packages"]
        cls.summaries = cls.kwargs["summaries"]

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.packages.delete()
        cls.summaries.delete()

    def setUp(self):
        super().setUp()
        self.db = DynamoCache(DummyRequest(), **self.kwargs)
        self.storage = self.db.storage = MagicMock(spec=IStorage)

    def tearDown(self):
        super().tearDown()
        clear_dynamo_table(self.packages, "filename")
        clear_dynamo_table(self.summaries, "name")

    def _save_pkgs(self, *pkgs):
        """Save a DynamoPackage to the db"""
        for pkg in pkgs:
            self.packages.put_item(Item=pkg.to_item())
            self.summaries.put_item(Item=PackageSummary(pkg).to_item())

    def test_add_missing(self):
        """Add missing packages to cache"""
        keys = [make_package(factory=DynamoPackage)]
        self.storage.list.return_value = keys
        self.db.reload_from_storage()
        all_pkgs = scan_packages(self.packages)
        self.assertCountEqual(all_pkgs, keys)
        all_summaries = self.summaries.scan()["Items"]
        self.assertEqual(len(all_summaries), 1)

    def test_add_missing_more_than_one_batch(self):
        """Add missing packages when they span several write and read batches"""
        # More names than one BatchGetItem request can carry, so the summary
        # fetch has to chunk them
        count = BATCH_GET_SIZE + 50
        keys = [
            make_package(
                f"pkg{i}", filename=f"pkg{i}-1.1.tar.gz", factory=DynamoPackage
            )
            for i in range(count)
        ]
        self.storage.list.return_value = keys
        self.db.reload_from_storage()
        self.assertCountEqual(scan_packages(self.packages), keys)
        self.assertEqual(len(list(_iter_items(self.summaries))), count)

    def test_remove_extra(self):
        """Remove extra packages from cache"""
        keys = [
            make_package(factory=DynamoPackage),
            make_package("mypkg2", "1.3.4", factory=DynamoPackage),
        ]
        self.db.save(keys[0])
        self.db.save(keys[1])
        self.storage.list.return_value = keys[:1]
        self.db.reload_from_storage()
        all_pkgs = scan_packages(self.packages)
        self.assertCountEqual(all_pkgs, keys[:1])
        # It should have removed the summary as well
        self.assertEqual(self.summaries.scan()["Count"], 1)

    def test_remove_extra_leave_concurrent(self):
        """Removing extra packages will leave packages that were uploaded concurrently"""
        pkgs = [
            make_package(factory=DynamoPackage),
            make_package("mypkg2", factory=DynamoPackage),
        ]
        self.db.save(pkgs[0])
        self.db.save(pkgs[1])

        # Return first pkgs[1], then pkgs[1:] because the second time we list
        # we will have "uploaded" pkgs[2]
        return_values = [lambda: pkgs[1:2], lambda: pkgs[1:]]

        def list_storage(factory):
            """mocked method for listing storage packages"""
            # The first time we list from storage, concurrently "upload"
            # pkgs[2]
            if len(return_values) == 2:
                pkg = make_package("mypkg3", factory=DynamoPackage)
                pkgs.append(pkg)
                self.db.save(pkg)
            return return_values.pop(0)()

        self.storage.list.side_effect = list_storage

        self.db.reload_from_storage()
        all_pkgs = scan_packages(self.packages)
        self.assertCountEqual(all_pkgs, pkgs[1:])
        self.assertEqual(self.summaries.scan()["Count"], 2)

    def test_remove_extra_concurrent_deletes(self):
        """Remove packages from cache that were concurrently deleted"""
        pkgs = [
            make_package(factory=DynamoPackage),
            make_package("mypkg2", factory=DynamoPackage),
        ]
        self.db.save(pkgs[0])

        # Return first pkgs[:], then pkgs[:1] because the second time we list
        # we will have "deleted" pkgs[1]
        return_values = [pkgs[:], pkgs[:1]]
        self.storage.list.side_effect = lambda _: return_values.pop(0)

        self.db.reload_from_storage()
        all_pkgs = scan_packages(self.packages)
        self.assertCountEqual(all_pkgs, pkgs[:1])
        self.assertEqual(self.summaries.scan()["Count"], 1)

    def test_add_missing_more_recent(self):
        """If we sync a more recent package, update the summary"""
        pkgs = [
            make_package(
                last_modified=datetime.now(UTC) - timedelta(hours=1),
                factory=DynamoPackage,
            ),
            make_package(version="1.5", factory=DynamoPackage),
        ]
        self.db.save(pkgs[0])
        self.storage.list.return_value = pkgs
        self.db.reload_from_storage()
        all_pkgs = scan_packages(self.packages)
        self.assertCountEqual(all_pkgs, pkgs)
        summaries = self.db.summary()
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["last_modified"], pkgs[1].last_modified)

    def test_same_package_name_version(self):
        """Storage can have packages with the same name and version (different filename)"""
        pkgs = [
            make_package(filename="mypkg-1.1-win32.whl", factory=DynamoPackage),
            make_package(filename="mypkg-1.1-macosx.whl", factory=DynamoPackage),
            make_package(filename="mypkg-1.1-x86_64.whl", factory=DynamoPackage),
        ]
        self.storage.list.return_value = pkgs
        self.db.reload_from_storage()
        all_pkgs = scan_packages(self.packages)
        self.assertCountEqual(all_pkgs, pkgs)
        summaries = self.db.summary()
        self.assertEqual(len(summaries), 1)


class TestRedisCache(unittest.TestCase):
    """Tests for the RedisCache"""

    @classmethod
    def extra_settings(cls):
        return {}

    @classmethod
    def get_redis_url(cls):
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        redis_port = os.environ.get("REDIS_PORT", "6379")

        return f"redis://{redis_host}:{redis_port}"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        redis_url = cls.get_redis_url()

        settings = {
            "pypi.storage": "tests.DummyStorage",
            "db.url": redis_url,
            "db.graceful_reload": True,
            **cls.extra_settings(),
        }
        cls.kwargs = RedisCache.configure(settings)
        cls.redis = cls.kwargs["db"]

        try:
            cls.redis.flushdb()
        except redis.exceptions.ConnectionError:
            msg = f"Redis not found on {redis_url}"
            cls.setUp = lambda cls: unittest.TestCase.skipTest(cls, msg)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.db = RedisCache(DummyRequest(), **self.kwargs)
        self.storage = self.db.storage = MagicMock(spec=IStorage)

    def tearDown(self):
        super().tearDown()
        self.redis.flushdb()

    def _save_pkgs(self, *pkgs):
        """Save packages to the db"""
        pipe = self.redis.pipeline()
        for pkg in pkgs:
            self.db.save(pkg, pipe)
        pipe.execute()

    def test_add_missing(self):
        """Add missing packages to cache"""
        keys = [make_package()]
        self.storage.list.return_value = keys
        self.db.reload_from_storage()
        all_pkgs = self.db._load_all_packages()
        self.assertCountEqual(all_pkgs, keys)
        self.assertEqual(len(self.db.summary()), 1)

    def test_remove_extra(self):
        """Remove extra packages from cache"""
        keys = [make_package(), make_package("mypkg2", "1.3.4")]
        self.db.save(keys[0])
        self.db.save(keys[1])
        self.storage.list.return_value = keys[:1]
        self.db.reload_from_storage()
        all_pkgs = self.db._load_all_packages()
        self.assertCountEqual(all_pkgs, keys[:1])
        # It should have removed the summary as well
        self.assertEqual(len(self.db.summary()), 1)

    def test_remove_extra_leave_concurrent(self):
        """Removing extra packages will leave packages that were uploaded concurrently"""
        pkgs = [make_package(), make_package("mypkg2")]
        self.db.save(pkgs[0])
        self.db.save(pkgs[1])

        # Return first pkgs[1], then pkgs[1:] because the second time we list
        # we will have "uploaded" pkgs[2]
        return_values = [lambda: pkgs[1:2], lambda: pkgs[1:]]

        def list_storage(factory):
            """mocked method for listing storage packages"""
            # The first time we list from storage, concurrently "upload"
            # pkgs[2]
            if len(return_values) == 2:
                pkg = make_package("mypkg3")
                pkgs.append(pkg)
                self.db.save(pkg)
            return return_values.pop(0)()

        self.storage.list.side_effect = list_storage

        self.db.reload_from_storage()
        all_pkgs = self.db._load_all_packages()
        self.assertCountEqual(all_pkgs, pkgs[1:])
        self.assertEqual(len(self.db.summary()), 2)

    def test_remove_extra_concurrent_deletes(self):
        """Remove packages from cache that were concurrently deleted"""
        pkgs = [make_package(), make_package("mypkg2")]
        self.db.save(pkgs[0])

        # Return first pkgs[:], then pkgs[:1] because the second time we list
        # we will have "deleted" pkgs[1]
        return_values = [pkgs[:], pkgs[:1]]
        self.storage.list.side_effect = lambda _: return_values.pop(0)

        self.db.reload_from_storage()
        all_pkgs = self.db._load_all_packages()
        self.assertCountEqual(all_pkgs, pkgs[:1])
        self.assertEqual(len(self.db.summary()), 1)

    def test_add_missing_more_recent(self):
        """If we sync a more recent package, update the summary"""
        pkgs = [
            make_package(last_modified=datetime.now(UTC) - timedelta(hours=1)),
            make_package(version="1.5"),
        ]
        self.db.save(pkgs[0])
        self.storage.list.return_value = pkgs
        self.db.reload_from_storage()
        all_pkgs = self.db._load_all_packages()
        self.assertCountEqual(all_pkgs, pkgs)
        summaries = self.db.summary()
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["last_modified"].hour, pkgs[1].last_modified.hour)

    def test_same_package_name_version(self):
        """Storage can have packages with the same name and version (different filename)"""
        pkgs = [
            make_package(filename="mypkg-1.1-win32.whl"),
            make_package(filename="mypkg-1.1-macosx.whl"),
            make_package(filename="mypkg-1.1-x86_64.whl"),
        ]
        self.storage.list.return_value = pkgs
        self.db.reload_from_storage()
        all_pkgs = self.db._load_all_packages()
        self.assertCountEqual(all_pkgs, pkgs)
        summaries = self.db.summary()
        self.assertEqual(len(summaries), 1)


class TestClusteredRedisCache(TestRedisCache):
    """Tests for the clustered redis cache"""

    @classmethod
    def extra_settings(cls):
        return {"db.clustered": "true"}

    @classmethod
    def get_redis_url(cls):
        redis_host = os.environ.get("REDIS_CLUSTER_HOST", "localhost")
        redis_port = os.environ.get("REDIS_PORT", "6379")

        return f"redis://{redis_host}:{redis_port}"


class TestSQLiteCache(unittest.TestCase):
    """Tests for the SQLCache"""

    @classmethod
    def get_db_url(cls) -> str:
        return get_sqlite_url()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db_url = cls.get_db_url()
        settings = EnvironSettings(
            {
                "pypi.storage": "tests.DummyStorage",
                "db.url": db_url,
                "db.graceful_reload": True,
            },
            {},
        )
        try:
            cls.kwargs = SQLCache.configure(settings)
        except OperationalError as e:
            raise unittest.SkipTest(f"Couldn't connect to database {db_url}") from e

    def setUp(self):
        super().setUp()
        transaction.begin()
        self.request = DummyRequest()
        self.request.tm = transaction.manager
        self.db = SQLCache(self.request, **self.kwargs)
        self.sql = self.db.db
        self.storage = self.db.storage = MagicMock(spec=IStorage)

    def tearDown(self):
        super().tearDown()
        transaction.abort()
        self.sql.query(SQLPackage).delete()
        transaction.commit()
        self.request._process_finished_callbacks()

    def _make_package(self, *args, **kwargs):
        """Wrapper around make_package"""
        # Some SQL dbs are rounding the timestamps (looking at you MySQL >:|
        # which is a problem if they round UP to the future, as our
        # calculations depend on the timestamps being monotonically increasing.
        now = datetime.now(UTC) - timedelta(seconds=1)
        kwargs.setdefault("last_modified", now)
        kwargs.setdefault("factory", SQLPackage)
        return make_package(*args, **kwargs)

    def test_add_missing(self):
        """Add missing packages to cache"""
        keys = [self._make_package()]
        self.storage.list.return_value = keys
        self.db.reload_from_storage()
        all_pkgs = self.sql.query(SQLPackage).all()
        self.assertCountEqual(all_pkgs, keys)

    def test_remove_extra(self):
        """Remove extra packages from cache"""
        keys = [self._make_package(), self._make_package("mypkg2", "1.3.4")]
        self.db.save(keys[0])
        self.db.save(keys[1])
        self.storage.list.return_value = keys[:1]
        self.db.reload_from_storage()
        all_pkgs = self.sql.query(SQLPackage).all()
        self.assertCountEqual(all_pkgs, keys[:1])

    def test_remove_extra_leave_concurrent(self):
        """Removing extra packages will leave packages that were uploaded concurrently"""
        pkgs = [self._make_package(), self._make_package("mypkg2")]
        self.db.save(pkgs[0])
        self.db.save(pkgs[1])

        # Return first pkgs[1], then pkgs[1:] because the second time we list
        # we will have "uploaded" pkgs[2]
        return_values = [lambda: pkgs[1:2], lambda: pkgs[1:]]

        def list_storage(factory):
            """mocked method for listing storage packages"""
            # The first time we list from storage, concurrently "upload"
            # pkgs[2]
            if len(return_values) == 2:
                nowish = datetime.now(UTC) + timedelta(seconds=1)
                pkg = self._make_package("mypkg3", last_modified=nowish)
                pkgs.append(pkg)
                self.db.save(pkg)
            return return_values.pop(0)()

        self.storage.list.side_effect = list_storage

        self.db.reload_from_storage()
        all_pkgs = self.sql.query(SQLPackage).all()
        self.assertCountEqual(all_pkgs, pkgs[1:])

    def test_remove_extra_concurrent_deletes(self):
        """Remove packages from cache that were concurrently deleted"""
        pkgs = [self._make_package(), self._make_package("mypkg2")]
        self.db.save(pkgs[0])

        # Return first pkgs[:], then pkgs[:1] because the second time we list
        # we will have "deleted" pkgs[1]
        return_values = [pkgs[:], pkgs[:1]]
        self.storage.list.side_effect = lambda _: return_values.pop(0)

        self.db.reload_from_storage()
        all_pkgs = self.sql.query(SQLPackage).all()
        self.assertCountEqual(all_pkgs, pkgs[:1])

    def test_add_missing_more_recent(self):
        """If we sync a more recent package, update the summary"""
        pkgs = [
            self._make_package(last_modified=datetime.now(UTC) - timedelta(hours=1)),
            self._make_package(version="1.5"),
        ]
        self.db.save(pkgs[0])
        self.storage.list.return_value = pkgs
        self.db.reload_from_storage()
        all_pkgs = self.sql.query(SQLPackage).all()
        self.assertCountEqual(all_pkgs, pkgs)

    def test_same_package_name_version(self):
        """Storage can have packages with the same name and version (different filename)"""
        pkgs = [
            self._make_package(filename="mypkg-1.1-win32.whl"),
            self._make_package(filename="mypkg-1.1-macosx.whl"),
            self._make_package(filename="mypkg-1.1-x86_64.whl"),
        ]
        self.storage.list.return_value = pkgs
        self.db.reload_from_storage()
        all_pkgs = self.sql.query(SQLPackage).all()
        self.assertCountEqual(all_pkgs, pkgs)


class TestMySQLCache(TestSQLiteCache):
    """Test the SQLAlchemy cache on a MySQL DB"""

    @classmethod
    def get_db_url(cls) -> str:
        return get_mysql_url()


class TestPostgresCache(TestSQLiteCache):
    """Test the SQLAlchemy cache on a Postgres DB"""

    @classmethod
    def get_db_url(cls) -> str:
        return get_postgres_url()

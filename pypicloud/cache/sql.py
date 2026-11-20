"""Store package data in a SQL database"""

import json
import logging
from datetime import UTC, datetime
from typing import override

import zope.sqlalchemy
from pyramid.path import DottedNameResolver
from pyramid.settings import asbool
from sqlalchemy import (
    DateTime,
    String,
    Text,
    and_,
    delete,
    engine_from_config,
    or_,
    select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.mutable import Mutable
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.sql import func
from sqlalchemy.types import TEXT, TypeDecorator

from pypicloud.models import Package
from pypicloud.util import EnvironSettings

from .base import ICache

LOG = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for the cache tables"""


class JSONEncodedDict(TypeDecorator):
    "Represents an immutable structure as a json-encoded string."

    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            value = json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            value = json.loads(value)
        return value


class MutableDict(Mutable, dict):
    """SQLAlchemy dict field that tracks changes"""

    @classmethod
    def coerce(cls, key, value):
        "Convert plain dictionaries to MutableDict."

        if not isinstance(value, MutableDict):
            if isinstance(value, dict):
                return MutableDict(value)

            # this call will raise ValueError
            return Mutable.coerce(key, value)
        else:
            return value

    def __setitem__(self, key, value):
        "Detect dictionary set events and emit change events."

        dict.__setitem__(self, key, value)
        self.changed()

    def __delitem__(self, key):
        "Detect dictionary del events and emit change events."

        dict.__delitem__(self, key)
        self.changed()


MutableDict.associate_with(JSONEncodedDict)


class TZAwareDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value, dialect):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class SQLPackage(Package, Base):
    """Python package stored in SQLAlchemy"""

    __tablename__ = "packages"
    filename: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(1000), nullable=False)
    last_modified: Mapped[datetime] = mapped_column(
        TZAwareDateTime(), index=True, nullable=False
    )
    # TEXT, as pypi does the same, and using String(N) would mismatch with pypi
    summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    data: Mapped[dict] = mapped_column(JSONEncodedDict(), nullable=False)


def create_schema(engine):
    """
    Create the database schema if needed

    Parameters
    ----------
    engine : :class:`sqlalchemy.Engine`

    Notes
    -----
    The method should only be called after importing all modules containing
    models which extend the ``Base`` object.

    """
    Base.metadata.create_all(bind=engine)


def drop_schema(engine):
    """
    Drop the database schema

    Parameters
    ----------
    engine : :class:`sqlalchemy.Engine`

    Notes
    -----
    The method should only be called after importing all modules containing
    models which extend the ``Base`` object.

    """
    Base.metadata.drop_all(bind=engine)


class SQLCache(ICache):
    """Caching database that uses SQLAlchemy"""

    def __init__(self, request=None, dbmaker=None, graceful_reload=False, **kwargs):
        super().__init__(request, **kwargs)
        self.dbmaker = dbmaker
        self.db = self.dbmaker()
        self.graceful_reload = graceful_reload

        if request is not None:
            zope.sqlalchemy.register(self.db, transaction_manager=request.tm)

    @override
    def new_package(self, *args, **kwargs):
        return SQLPackage(*args, **kwargs)

    @override
    def reload_if_needed(self):
        super().reload_if_needed()
        if self.request is None:
            self.db.commit()
            self.db.close()

    @classmethod
    @override
    def configure(cls, settings: EnvironSettings):
        kwargs = super().configure(settings)
        graceful_reload = asbool(settings.pop("db.graceful_reload", False))
        settings.read_prefix_from_environ("db.")
        engine_opts = {}
        poolclass = settings.pop("db.poolclass", None)
        if poolclass is not None:
            resolver = DottedNameResolver(__name__)
            engine_opts["poolclass"] = resolver.maybe_resolve(poolclass)
        engine = engine_from_config(dict(settings), prefix="db.", **engine_opts)
        # Create SQL schema if not exists
        create_schema(engine)
        kwargs["dbmaker"] = sessionmaker(bind=engine)
        kwargs["graceful_reload"] = graceful_reload
        return kwargs

    @classmethod
    @override
    def postfork(cls, **kwargs):
        # Have to dispose of connections after uWSGI forks,
        # otherwise they'll get corrupted.
        kwargs["dbmaker"].kw["bind"].dispose()

    @override
    def fetch(self, filename):
        return self.db.scalars(select(SQLPackage).filter_by(filename=filename)).first()

    @override
    def all(self, name):
        pkgs = list(self.db.scalars(select(SQLPackage).filter_by(name=name)))
        pkgs.sort(reverse=True)
        return pkgs

    @override
    def distinct(self):
        names = select(SQLPackage.name).distinct().order_by(SQLPackage.name)
        return list(self.db.scalars(names))

    @override
    def search(self, criteria, query_type):
        """
        Perform a search.

        Queries are performed as follows:

            For the AND query_type, queries within a column will utilize the
            AND operator, but will not conflict with queries in another column.

                (column1 LIKE '%a%' AND column1 LIKE '%b%')
                OR
                (column2 LIKE '%c%' AND column2 LIKE '%d%')

            For the OR query_type, all queries will utilize the OR operator:

                (column1 LIKE '%a%' OR column1 LIKE '%b%')
                OR
                (column2 LIKE '%c%' OR column2 LIKE '%d%')

        """
        conditions = []
        for key, queries in criteria.items():
            # Make sure search key exists in the package class.
            # It should be either "name" or "summary".
            field = getattr(SQLPackage, key, None)
            if not field:
                continue

            column_conditions = []

            for query in queries:
                # Generate condition and add to list
                condition = field.like("%" + query + "%")
                column_conditions.append(condition)

            # Check if conditions for this column were generated
            if column_conditions:
                operator = and_ if query_type == "and" else or_
                conditions.append(operator(*column_conditions))

        # Piece together the queries. Refer to the method docstring for
        # examples as to how this works.
        results = self.db.scalars(select(SQLPackage).where(or_(*conditions)))

        # Extract only the most recent version for each package
        latest_map = {}
        for package in results:
            if package.name not in latest_map or package > latest_map[package.name]:
                latest_map[package.name] = package

        return latest_map.values()

    @override
    def summary(self):
        subquery = (
            select(
                SQLPackage.name,
                func.max(SQLPackage.last_modified).label("last_modified"),
            )
            .group_by(SQLPackage.name)
            .subquery()
        )
        rows = self.db.execute(
            select(SQLPackage.name, SQLPackage.last_modified, SQLPackage.summary).where(
                (SQLPackage.name == subquery.c.name)
                & (SQLPackage.last_modified == subquery.c.last_modified)
            )
        )

        # Dedupe because two packages may share the same last_modified
        seen_packages = set()
        packages = []
        for row in rows:
            if row.name in seen_packages:
                continue
            seen_packages.add(row.name)
            packages.append(
                {
                    "name": row.name,
                    "last_modified": row.last_modified,
                    "summary": row.summary,
                }
            )
        return packages

    @override
    def clear(self, package):
        self.db.delete(package)

    @override
    def clear_all(self):
        # Release any transactions before we go reloading schema
        if self.request is None:
            self.db.rollback()
        else:
            self.request.tm.abort()
        engine = self.dbmaker.kw["bind"]
        drop_schema(engine)
        create_schema(engine)

    @override
    def save(self, package):
        self.db.merge(package)

    @override
    def reload_from_storage(self, clear=True):
        if not self.graceful_reload:
            return super().reload_from_storage(clear)

        LOG.info("Rebuilding cache from storage")
        # Log start time
        start = datetime.now(UTC)
        # Fetch packages from storage s1
        s1 = set(self.storage.list(SQLPackage))
        # Fetch cache packages c1
        c1 = set(self.db.scalars(select(SQLPackage)))
        # Add missing packages to cache (s1 - c1)
        missing = s1 - c1
        if missing:
            LOG.info("Adding %d missing packages to cache", len(missing))
            for pkg in missing:
                self.db.merge(pkg)
        # Delete extra packages from cache (c1 - s1) when last_modified < start
        # The time filter helps us avoid deleting packages that were
        # concurrently uploaded.
        extra1 = [p for p in (c1 - s1) if p.last_modified <= start]
        if extra1:
            LOG.info("Removing %d extra packages from cache", len(extra1))
            self._delete_packages(extra1)

        # If any packages were concurrently deleted during the cache rebuild,
        # we can detect them by polling storage again and looking for any
        # packages that were present in s1 and are missing from s2
        s2 = set(self.storage.list(SQLPackage))
        # Delete extra packages from cache (s1 - s2)
        extra2 = s1 - s2
        if extra2:
            LOG.info(
                "Removing %d packages from cache that were concurrently "
                "deleted during rebuild",
                len(extra2),
            )
            self._delete_packages(extra2)

    def _delete_packages(self, packages):
        """Remove packages from the cache by filename"""
        filenames = [package.filename for package in packages]
        self.db.execute(
            delete(SQLPackage).where(SQLPackage.filename.in_(filenames)),
            execution_options={"synchronize_session": False},
        )

    @override
    def check_health(self):
        try:
            self.db.scalars(select(SQLPackage).limit(1)).first()
        except SQLAlchemyError as e:
            return (False, str(e))
        else:
            return (True, "")

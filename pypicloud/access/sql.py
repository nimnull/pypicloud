"""Access backend for storing permissions in using SQLAlchemy"""

from typing import override

import zope.sqlalchemy
from pyramid.path import DottedNameResolver
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    String,
    Table,
    Text,
    engine_from_config,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    backref,
    mapped_column,
    relationship,
    sessionmaker,
)

from pypicloud.util import EnvironSettings

from .base import IMutableAccessBackend


class Base(DeclarativeBase):
    """Declarative base for the access-control tables"""


association_table = Table(
    "pypicloud_user_groups",
    Base.metadata,
    Column(
        "username",
        String(length=255),
        ForeignKey("pypicloud_users.username", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group",
        String(length=255),
        ForeignKey("pypicloud_groups.name", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class KeyVal(Base):
    """Simple model for storing key-value pairs"""

    __tablename__ = "pypicloud_keyvals"
    key: Mapped[str] = mapped_column(String(length=255), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text())

    def __init__(self, key, value):
        self.key = key
        self.value = value


class User(Base):
    """User record"""

    __tablename__ = "pypicloud_users"
    username: Mapped[str] = mapped_column(String(length=255), primary_key=True)
    password: Mapped[str] = mapped_column("password", Text(), nullable=False)
    admin: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    pending: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    groups: Mapped[set[Group]] = relationship(
        "Group",
        secondary=association_table,
        cascade="all",
        collection_class=set,
        backref=backref("users", collection_class=set),
    )

    def __init__(self, username, password, pending=True):
        self.username = username
        self.password = password
        self.groups = set()
        self.permissions = []
        self.admin = False
        self.pending = pending


class Group(Base):
    """Group record"""

    __tablename__ = "pypicloud_groups"
    name: Mapped[str] = mapped_column(String(length=255), primary_key=True)

    def __init__(self, name):
        self.name = name
        self.users = set()
        self.permissions = []


class Permission(Base):
    """Base class for user and group permissions"""

    __abstract__ = True
    package: Mapped[str] = mapped_column(String(length=255), primary_key=True)
    read: Mapped[bool | None] = mapped_column(Boolean())
    write: Mapped[bool | None] = mapped_column(Boolean())

    def __init__(self, package, read, write):
        self.package = package
        self.read = read
        self.write = write

    @property
    def permissions(self):
        """Construct permissions list"""
        perms = []
        if self.read:
            perms.append("read")
        if self.write:
            perms.append("write")
        return perms


class UserPermission(Permission):
    """Permissions for a user on a package"""

    __tablename__ = "pypicloud_user_permissions"
    username: Mapped[str] = mapped_column(
        String(length=255),
        ForeignKey(User.username, ondelete="CASCADE"),
        primary_key=True,
    )
    user: Mapped[User] = relationship(
        "User", backref=backref("permissions", cascade="all, delete-orphan")
    )

    def __init__(self, package, username, read=False, write=False):
        super().__init__(package, read, write)
        self.username = username


class GroupPermission(Permission):
    """Permissions for a group on a package"""

    __tablename__ = "pypicloud_group_permissions"
    groupname: Mapped[str] = mapped_column(
        String(length=255), ForeignKey(Group.name, ondelete="CASCADE"), primary_key=True
    )
    group: Mapped[Group] = relationship(
        "Group", backref=backref("permissions", cascade="all, delete-orphan")
    )

    def __init__(self, package, groupname, read=False, write=False):
        super().__init__(package, read, write)
        self.groupname = groupname


class SQLAccessBackend(IMutableAccessBackend):
    """
    This backend allows you to store all user and package permissions in a SQL
    database

    """

    def __init__(self, request=None, dbmaker=None, **kwargs):
        super().__init__(request, **kwargs)
        self._db = None
        self._dbmaker = dbmaker

    @property
    def db(self):
        """Lazy-create the DB session"""
        if self._db is None:
            self._db = self._dbmaker()
            if self.request is not None:
                zope.sqlalchemy.register(self._db, transaction_manager=self.request.tm)
        return self._db

    @classmethod
    @override
    def configure(cls, settings: EnvironSettings):
        kwargs = super().configure(settings)
        settings.read_prefix_from_environ("auth.db.")
        engine_opts = {}
        poolclass = settings.pop("auth.db.poolclass", None)
        if poolclass is not None:
            resolver = DottedNameResolver(__name__)
            engine_opts["poolclass"] = resolver.maybe_resolve(poolclass)
        engine = engine_from_config(dict(settings), prefix="auth.db.", **engine_opts)
        kwargs["dbmaker"] = sessionmaker(bind=engine)
        # Create SQL schema if not exists
        Base.metadata.create_all(bind=engine)
        return kwargs

    @classmethod
    @override
    def postfork(cls, **kwargs):
        # Have to dispose of connections after uWSGI forks,
        # otherwise they'll get corrupted.
        kwargs["dbmaker"].kw["bind"].dispose()

    @override
    def allow_register(self):
        ret = self.db.query(KeyVal).filter_by(key="allow_register").first()
        return ret is not None and ret.value == "true"

    @override
    def set_allow_register(self, allow):
        if allow:
            k = KeyVal("allow_register", "true")
            self.db.merge(k)
        else:
            self.db.query(KeyVal).filter_by(key="allow_register").delete()

    @override
    def _get_password_hash(self, username):
        user = self.db.query(User).filter_by(username=username).first()
        if user:
            return user.password

    @override
    def groups(self, username=None):
        if username is None:
            query = self.db.query(Group)
            return [g.name for g in query]
        else:
            user = self.db.query(User).filter_by(username=username).first()
            if user is None:
                return []
            return [g.name for g in user.groups]

    @override
    def group_members(self, group):
        g = self.db.query(Group).filter_by(name=group).first()
        if not g:
            return []
        return [u.username for u in g.users]

    @override
    def is_admin(self, username):
        user = self.db.query(User).filter_by(username=username).first()
        return user and user.admin

    @override
    def group_permissions(self, package):
        query = self.db.query(GroupPermission).filter_by(package=package)
        perms = {}

        for perm in query:
            perms[perm.groupname] = perm.permissions
        return perms

    @override
    def user_permissions(self, package):
        query = self.db.query(UserPermission).filter_by(package=package)
        perms = {}
        for perm in query:
            perms[perm.username] = perm.permissions
        return perms

    @override
    def user_package_permissions(self, username):
        query = self.db.query(UserPermission).filter_by(username=username)
        packages = []
        for perm in query:
            packages.append({"package": perm.package, "permissions": perm.permissions})
        return packages

    @override
    def group_package_permissions(self, group):
        query = self.db.query(GroupPermission).filter_by(groupname=group)
        packages = []
        for perm in query:
            packages.append({"package": perm.package, "permissions": perm.permissions})
        return packages

    @override
    def user_data(self, username=None):
        if username is None:
            query = self.db.query(User).filter_by(pending=False)
            users = []
            for user in query:
                users.append({"username": user.username, "admin": user.admin})
            return users
        else:
            user = (
                self.db.query(User).filter_by(username=username, pending=False).first()
            )
            if user is not None:
                return {
                    "username": user.username,
                    "admin": user.admin,
                    "groups": [g.name for g in user.groups],
                }

    @override
    def need_admin(self):
        return self.db.query(User).filter_by(admin=True).first() is None

    @override
    def _register(self, username, password):
        user = User(username, password)
        self.db.add(user)

    @override
    def pending_users(self):
        query = self.db.query(User).filter_by(pending=True)
        return [u.username for u in query]

    @override
    def approve_user(self, username):
        user = self.db.query(User).filter_by(username=username).first()
        if user is not None:
            user.pending = False

    @override
    def _set_password_hash(self, username, password_hash):
        user = self.db.query(User).filter_by(username=username).first()
        if user is not None:
            user.password = password_hash

    @override
    def delete_user(self, username):
        self.db.query(User).filter_by(username=username).delete()
        clause = association_table.c.username == username
        self.db.execute(association_table.delete().where(clause))

    @override
    def set_user_admin(self, username, admin):
        user = self.db.query(User).filter_by(username=username).first()
        if user is not None:
            user.admin = admin

    @override
    def edit_user_group(self, username, group, add):
        user = self.db.query(User).filter_by(username=username).first()
        groupobj = self.db.query(Group).filter_by(name=group).first()
        if user is not None and groupobj is not None:
            if add:
                user.groups.add(groupobj)
            else:
                user.groups.remove(groupobj)

    @override
    def create_group(self, group):
        self.db.add(Group(group))

    @override
    def delete_group(self, group):
        self.db.query(Group).filter_by(name=group).delete()
        clause = association_table.c.group == group
        self.db.execute(association_table.delete().where(clause))

    @override
    def edit_user_permission(self, package_name, username, perm, add):
        record = (
            self.db.query(UserPermission)
            .filter_by(package=package_name, username=username)
            .first()
        )
        if record is None:
            if not add:
                return
            record = UserPermission(package_name, username)
            self.db.add(record)
        if perm == "read":
            record.read = add
        elif perm == "write":
            record.write = add
        else:
            raise ValueError(f"Unrecognized permission '{perm}'")
        if not record.read and not record.write:
            self.db.delete(record)

    @override
    def edit_group_permission(self, package_name, group, perm, add):
        record = (
            self.db.query(GroupPermission)
            .filter_by(package=package_name, groupname=group)
            .first()
        )
        if record is None:
            if not add:
                return
            record = GroupPermission(package_name, group)
            self.db.add(record)
        if perm == "read":
            record.read = add
        elif perm == "write":
            record.write = add
        else:
            raise ValueError(f"Unrecognized permission '{perm}'")
        if not record.read and not record.write:
            self.db.delete(record)

    @override
    def check_health(self):
        try:
            self.db.query(KeyVal).first()
        except SQLAlchemyError as e:
            return (False, str(e))
        else:
            return (True, "")

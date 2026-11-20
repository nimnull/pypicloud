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
    delete,
    engine_from_config,
    select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
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
        secondary=association_table,
        cascade="all",
        collection_class=set,
        back_populates="users",
    )
    permissions: Mapped[list[UserPermission]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
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
    users: Mapped[set[User]] = relationship(
        secondary=association_table, collection_class=set, back_populates="groups"
    )
    permissions: Mapped[list[GroupPermission]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

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
    user: Mapped[User] = relationship(back_populates="permissions")

    def __init__(self, package, username, read=False, write=False):
        super().__init__(package, read, write)
        self.username = username


class GroupPermission(Permission):
    """Permissions for a group on a package"""

    __tablename__ = "pypicloud_group_permissions"
    groupname: Mapped[str] = mapped_column(
        String(length=255), ForeignKey(Group.name, ondelete="CASCADE"), primary_key=True
    )
    group: Mapped[Group] = relationship(back_populates="permissions")

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

    def _get(self, model, **filters):
        """The first row of a model matching the filters, or None"""
        return self.db.scalars(select(model).filter_by(**filters)).first()

    def _select(self, model, **filters):
        """Every row of a model matching the filters"""
        return self.db.scalars(select(model).filter_by(**filters))

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
        ret = self._get(KeyVal, key="allow_register")
        return ret is not None and ret.value == "true"

    @override
    def set_allow_register(self, allow):
        if allow:
            k = KeyVal("allow_register", "true")
            self.db.merge(k)
        else:
            self.db.execute(delete(KeyVal).where(KeyVal.key == "allow_register"))

    @override
    def _get_password_hash(self, username):
        user = self._get(User, username=username)
        if user:
            return user.password

    @override
    def groups(self, username=None):
        if username is None:
            return [g.name for g in self.db.scalars(select(Group))]
        else:
            user = self._get(User, username=username)
            if user is None:
                return []
            return [g.name for g in user.groups]

    @override
    def group_members(self, group):
        g = self._get(Group, name=group)
        if not g:
            return []
        return [u.username for u in g.users]

    @override
    def is_admin(self, username):
        user = self._get(User, username=username)
        return user and user.admin

    @override
    def group_permissions(self, package):
        query = self._select(GroupPermission, package=package)
        perms = {}

        for perm in query:
            perms[perm.groupname] = perm.permissions
        return perms

    @override
    def user_permissions(self, package):
        query = self._select(UserPermission, package=package)
        perms = {}
        for perm in query:
            perms[perm.username] = perm.permissions
        return perms

    @override
    def user_package_permissions(self, username):
        query = self._select(UserPermission, username=username)
        packages = []
        for perm in query:
            packages.append({"package": perm.package, "permissions": perm.permissions})
        return packages

    @override
    def group_package_permissions(self, group):
        query = self._select(GroupPermission, groupname=group)
        packages = []
        for perm in query:
            packages.append({"package": perm.package, "permissions": perm.permissions})
        return packages

    @override
    def user_data(self, username=None):
        if username is None:
            query = self._select(User, pending=False)
            users = []
            for user in query:
                users.append({"username": user.username, "admin": user.admin})
            return users
        else:
            user = self._get(User, username=username, pending=False)
            if user is not None:
                return {
                    "username": user.username,
                    "admin": user.admin,
                    "groups": [g.name for g in user.groups],
                }

    @override
    def need_admin(self):
        return self._get(User, admin=True) is None

    @override
    def _register(self, username, password):
        user = User(username, password)
        self.db.add(user)

    @override
    def pending_users(self):
        return [u.username for u in self._select(User, pending=True)]

    @override
    def approve_user(self, username):
        user = self._get(User, username=username)
        if user is not None:
            user.pending = False

    @override
    def _set_password_hash(self, username, password_hash):
        user = self._get(User, username=username)
        if user is not None:
            user.password = password_hash

    @override
    def delete_user(self, username):
        self.db.execute(delete(User).where(User.username == username))
        clause = association_table.c.username == username
        self.db.execute(association_table.delete().where(clause))

    @override
    def set_user_admin(self, username, admin):
        user = self._get(User, username=username)
        if user is not None:
            user.admin = admin

    @override
    def edit_user_group(self, username, group, add):
        user = self._get(User, username=username)
        groupobj = self._get(Group, name=group)
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
        self.db.execute(delete(Group).where(Group.name == group))
        clause = association_table.c.group == group
        self.db.execute(association_table.delete().where(clause))

    @override
    def edit_user_permission(self, package_name, username, perm, add):
        record = self._get(UserPermission, package=package_name, username=username)
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
        record = self._get(GroupPermission, package=package_name, groupname=group)
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
            self.db.scalars(select(KeyVal).limit(1)).first()
        except SQLAlchemyError as e:
            return (False, str(e))
        else:
            return (True, "")

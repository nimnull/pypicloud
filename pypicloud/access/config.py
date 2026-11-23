"""Backend that reads access control rules from config file"""

import logging
from typing import Any, override

from pyramid.settings import aslist

from .base_json import IJsonAccessBackend

LOG = logging.getLogger(__name__)


class ConfigAccessBackend(IJsonAccessBackend):
    """Access Backend that uses values set in the config file"""

    def __init__(self, request=None, data=None, **kwargs):
        super().__init__(request, **kwargs)
        self._data = data

    @classmethod
    @override
    def configure(cls, settings):
        kwargs = super().configure(settings)
        data = {}

        users = {}
        for key, value in settings.items():
            if not key.startswith("user."):
                continue
            users[key[len("user.") :]] = value
        data["users"] = users

        data["admins"] = aslist(settings.get("auth.admins", []))

        groups = {}
        for key, value in settings.items():
            if not key.startswith("group."):
                continue
            groups[key[len("group.") :]] = aslist(value)
        data["groups"] = groups

        packages: dict[str, Any] = {}
        for key, value in settings.items():
            pieces = key.split(".")
            if len(pieces) != 4 or pieces[0] != "package":
                continue
            _, package, mode, entity = pieces
            pkg_perms = packages.setdefault(package, {"users": {}, "groups": {}})
            if mode == "user":
                pkg_perms["users"][entity] = cls._perms_from_short(value)
            elif mode == "group":
                pkg_perms["groups"][entity] = cls._perms_from_short(value)
        data["packages"] = packages

        kwargs["data"] = data
        return kwargs

    @override
    def _get_db(self):
        return self._data

    @staticmethod
    def _perms_from_short(value):
        """Convert a 'r' or 'rw' specification to a list of permissions"""
        value = value or ""
        if len(value) > 2:
            return aslist(value)
        perms = []
        if "r" in value:
            perms.append("read")
        if "w" in value:
            perms.append("write")
        return perms

    @override
    def load(self, data):
        lines = []
        admins = []
        for user in data["users"]:
            lines.append("user.{username} = {password}".format(**user))
            if user.get("admin"):
                admins.append(user["username"])

        if admins:
            lines.append("auth.admins =")
            for admin in admins:
                lines.append(f"    {admin}")

        for group, members in data["groups"].items():
            lines.append(f"group.{group} =")
            for member in members:
                lines.append(f"    {member}")

        def encode_permissions(perms):
            """Encode a permissions list as the r/rw specification"""
            ret = ""
            if "read" in perms:
                ret += "r"
            if "write" in perms:
                ret += "w"
            return ret

        for package, groups in data["packages"]["groups"].items():
            for group, permissions in groups.items():
                lines.append(
                    f"package.{package}.group.{group} = {encode_permissions(permissions)}"
                )

        for package, users in data["packages"]["users"].items():
            for user, permissions in users.items():
                lines.append(
                    f"package.{package}.user.{user} = {encode_permissions(permissions)}"
                )

        return "\n".join(lines)

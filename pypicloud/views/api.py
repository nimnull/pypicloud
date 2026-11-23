"""Views for simple api calls that return json data"""

import logging
import posixpath
from tempfile import TemporaryFile
from typing import Any

from paste.httpheaders import CACHE_CONTROL, CONTENT_DISPOSITION
from pyramid.httpexceptions import (
    HTTPBadRequest,
    HTTPConflict,
    HTTPForbidden,
    HTTPNotFound,
)
from pyramid.response import FileIter
from pyramid.security import NO_PERMISSION_REQUIRED
from pyramid.view import view_config
from smart_open import open as _open

from pypicloud.access import Permission
from pypicloud.duh import addslash
from pypicloud.params import use_params
from pypicloud.route import (
    APIPackageFileResource,
    APIPackageResource,
    APIPackagingResource,
    APIResource,
)
from pypicloud.util import normalize_name, stream_file

from .login import handle_register_request

LOG = logging.getLogger(__name__)


@view_config(
    context=APIPackagingResource, request_method="GET", subpath=(), renderer="json"
)
@addslash
@use_params
def all_packages(request, verbose: bool = False):
    """List all packages"""
    packages = request.db.summary() if verbose else request.db.distinct()
    i = 0
    while i < len(packages):
        package = packages[i]
        name = package if isinstance(package, str) else package["name"]
        if not request.access.has_permission(name, Permission.READ):
            del packages[i]
            continue
        i += 1
    return {"packages": packages}


@view_config(
    context=APIPackageResource,
    request_method="GET",
    subpath=(),
    renderer="json",
    permission=Permission.READ,
)
@addslash
def package_versions(context, request):
    """List all unique package versions"""
    normalized_name = normalize_name(context.name)
    versions = request.db.all(normalized_name)
    return {
        "packages": versions,
        "write": request.access.has_permission(normalized_name, Permission.WRITE),
    }


def fetch_dist(request, url, name, version, summary, requires_python):
    """Fetch a Distribution and upload it to the storage backend"""
    filename = posixpath.basename(url)
    # The caller streams this to the client and closes it through a FileIter
    data_fp = TemporaryFile()  # noqa: SIM115

    with _open(url, "rb", compression="disable") as url_fp:
        for chunk in stream_file(url_fp):
            data_fp.write(chunk)

    # TODO: digest validation
    data_fp.seek(0)
    resp = request.db.upload(filename, data_fp, name, version, summary, requires_python)
    data_fp.seek(0)
    return resp, data_fp


@view_config(
    context=APIPackageFileResource, request_method="GET", permission=Permission.READ
)
def download_package(context, request):
    """Download package, or redirect to the download link"""
    package = request.db.fetch(context.filename)
    if not package:
        if request.registry.fallback != "cache":
            return HTTPNotFound()
        if not request.access.can_update_cache():
            return request.forbid()
        # If we are caching pypi, download the package from pypi and save it
        releases = request.locator.get_releases(context.name)

        dist = None
        for release in releases:
            if posixpath.basename(release["url"]) == context.filename:
                dist = release
                break
        if dist is None:
            return HTTPNotFound()
        LOG.info("Caching %s from %s", context.filename, request.fallback_simple)
        package, data_fp = fetch_dist(
            request,
            dist["url"],
            dist["name"],
            dist["version"],
            dist["summary"],
            dist["requires_python"],
        )
        disp = CONTENT_DISPOSITION.tuples(filename=package.filename)
        request.response.headers.update(disp)
        cache_control = CACHE_CONTROL.tuples(
            public=True, max_age=request.registry.package_max_age
        )
        request.response.headers.update(cache_control)
        request.response.app_iter = FileIter(data_fp)
        request.response.content_type = "application/octet-stream"
        return request.response
    if request.registry.stream_files:
        data_fp = request.db.storage.open(package)
        # FileIter closes the stream when the server finishes the response
        request.response.app_iter = FileIter(data_fp)
        disp = CONTENT_DISPOSITION.tuples(filename=package.filename)
        request.response.headers.update(disp)
        cache = CACHE_CONTROL.tuples(
            public=True, max_age=request.registry.package_max_age
        )
        request.response.headers.update(cache)
        request.response.content_type = "application/octet-stream"
        return request.response
    response = request.db.download_response(package)
    return response


@view_config(
    context=APIPackageFileResource,
    request_method="POST",
    subpath=(),
    renderer="json",
    permission=Permission.WRITE,
)
@use_params
def upload_package(
    context,
    request,
    content: Any,
    summary: str | None = None,
    requires_python: str | None = None,
):
    """Upload a package"""
    try:
        return request.db.upload(
            content.filename,
            content.file,
            name=context.name,
            summary=summary,
            requires_python=requires_python,
            uploader=request.authenticated_userid,
        )
    except ValueError as e:  # pragma: no cover
        return HTTPConflict(*e.args)


@view_config(
    context=APIPackageFileResource,
    request_method="DELETE",
    subpath=(),
    permission=Permission.WRITE,
)
def delete_package(context, request):
    """Delete a package"""
    package = request.db.fetch(context.filename)
    if package is None:
        return HTTPBadRequest(f"Could not find {context.filename}")

    try:
        request.db.delete(package)
    except ValueError:
        return HTTPForbidden("Package deletion is unallowed.")

    return request.response


@view_config(
    context=APIResource,
    name="user",
    request_method="PUT",
    subpath=("username/*"),
    renderer="json",
    permission=NO_PERMISSION_REQUIRED,
)
@use_params
def register(request, password: str):
    """Register a user"""
    username = request.named_subpaths["username"]
    return handle_register_request(request, username, password)


@view_config(
    context=APIResource,
    name="user",
    subpath=("password"),
    request_method="POST",
    permission=Permission.LOGIN,
)
@use_params
def change_password(request, old_password: str, new_password: str):
    """Change a user's password"""
    if not request.access.verify_user(request.authenticated_userid, old_password):
        return HTTPForbidden()
    request.access.edit_user_password(request.authenticated_userid, new_password)
    return request.response

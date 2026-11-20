"""View helpers: request parameters, subpath predicates, trailing slashes.

Vendored from pyramid_duh 0.1.2 (MIT, Steven Arcangeli), reduced to the parts
pypicloud uses.
"""

import fnmatch
import functools
import inspect
import re
from collections.abc import Callable
from typing import Any

from pyramid.config import Configurator
from pyramid.httpexceptions import HTTPBadRequest, HTTPFound
from pyramid.interfaces import IRequest
from pyramid.request import Request
from zope.interface.exceptions import DoesNotImplement
from zope.interface.verify import verifyObject

NO_ARG = object()
_ABSENT = object()


def is_request(obj: Any) -> bool:
    """True if obj provides pyramid's IRequest interface."""
    try:
        return verifyObject(IRequest, obj)
    except DoesNotImplement:
        return False


def _request_params(request: Request) -> Any:
    """Parameters of a request: the JSON body if it has one, else GET/POST params."""
    content_type = request.headers.get("Content-Type", "").split(";")[0]
    if content_type == "application/json":
        return request.json_body
    return request.params


def param(request: Request, name: str, default: Any = NO_ARG) -> Any:
    """Value of a single request parameter.

    Raises HTTPBadRequest if the parameter is missing and no default is given.
    """
    try:
        return _request_params(request)[name]
    except KeyError:
        if default is NO_ARG:
            raise HTTPBadRequest(f"Missing argument '{name}'") from None
        return default


def _positional_names(fxn: Callable) -> tuple[list[str], list[str], bool]:
    """Names of the required and optional parameters, and whether fxn takes **kwargs."""
    required: list[str] = []
    optional: list[str] = []
    takes_kwargs = False
    for name, parameter in inspect.signature(fxn).parameters.items():
        if parameter.kind is parameter.VAR_KEYWORD:
            takes_kwargs = True
        elif parameter.kind is parameter.VAR_POSITIONAL:
            continue
        elif parameter.default is parameter.empty:
            required.append(name)
        else:
            optional.append(name)
    return required, optional, takes_kwargs


def argify(fxn: Callable) -> Callable:
    """Pass request parameters into a view as named arguments.

    A missing required argument raises HTTPBadRequest; a missing optional one keeps
    its default. The arguments named ``self``, ``context`` and ``request`` get the
    view instance, the context and the request instead of a parameter. A call that
    does not follow pyramid's ``(context, request)`` or bound-method convention is
    passed through untouched, so tests can call the view directly.

    Values are passed through as they arrive: strings for form and query parameters,
    decoded JSON for a JSON body.
    """
    required, optional, takes_kwargs = _positional_names(fxn)

    @functools.wraps(fxn)
    def param_twiddler(*args: Any, **kwargs: Any) -> Any:
        view_self = None
        if required and required[0] == "self":
            if len(args) != 1 or kwargs:
                return fxn(*args, **kwargs)
            view_self = args[0]
            if not hasattr(view_self, "request"):
                raise AttributeError(f"View class {view_self} has no 'request'")
            request = view_self.request
            context = getattr(view_self, "context", None)
        elif len(args) == 2 and not kwargs and is_request(args[1]):
            context, request = args
        else:
            return fxn(*args, **kwargs)

        params = dict(_request_params(request))
        scope: dict[str, Any] = {}
        for name in required:
            if name == "self":
                scope[name] = view_self
            elif name == "context":
                scope[name] = context
            elif name == "request":
                scope[name] = request
            else:
                scope[name] = param(request, name)
                params.pop(name, None)
        for name in optional:
            value = param(request, name, _ABSENT)
            params.pop(name, None)
            if value is not _ABSENT:
                scope[name] = value
        if takes_kwargs:
            scope.update(params)
        return fxn(**scope)

    return param_twiddler


def addslash(fxn: Callable) -> Callable:
    """Redirect to the same url with a trailing slash before running the view.

    The query string is preserved. A call that does not follow pyramid's
    ``(context, request)`` convention is passed through untouched.
    """
    required, optional, _ = _positional_names(fxn)
    wants_request_only = len(required) + len(optional) == 1

    @functools.wraps(fxn)
    def slash_redirect(*args: Any, **kwargs: Any) -> Any:
        if len(args) == 2 and is_request(args[1]):
            request = args[1]
            if not request.path_url.endswith("/"):
                new_url = request.path_url + "/"
                if request.query_string:
                    new_url += "?" + request.query_string
                return HTTPFound(location=new_url)
            if wants_request_only:
                return fxn(request)
            return fxn(*args)
        return fxn(*args, **kwargs)

    return slash_redirect


def match(pattern: str, path: str | None, flags: str) -> Any:
    """Match one subpath segment against a pattern.

    Flags: 'r' matches with a regex instead of a glob, 'i' and 'a' add the
    IGNORECASE and ASCII regex flags, '?' makes a missing path segment match.
    Returns the regex match object for a successful regex match, else a bool.
    """
    if path is None:
        return "?" in flags
    if "r" in flags:
        re_flags = 0
        if "i" in flags:
            re_flags |= re.I
        if "a" in flags:
            re_flags |= re.A
        return re.match(f"^{pattern}$", path, re_flags)
    return fnmatch.fnmatchcase(path, pattern)


class SubpathPredicate:
    """View predicate that matches and names the segments of ``request.subpath``.

    Each spec is 'glob', 'name/glob' or 'name/glob/flags' (see :meth:`match` for the
    flags). Named segments land in ``request.named_subpaths``.
    """

    def __init__(self, paths: str | tuple[str, ...], config: Configurator):
        if isinstance(paths, str):
            paths = (paths,)
        self.paths = paths
        self.config = config

    def text(self) -> str:
        """Display name of the predicate."""
        return f"subpath = {self.paths}"

    phash = text

    def __call__(self, context: Any, request: Request) -> bool:
        named_subpaths = {}
        if len(request.subpath) > len(self.paths):
            return False
        for i, spec in enumerate(self.paths):
            pieces = spec.split("/", 2)
            if len(pieces) == 1:
                name, pattern, flags = None, pieces[0], ""
            elif len(pieces) == 2:
                name, pattern, flags = pieces[0], pieces[1], ""
            else:
                name, pattern, flags = pieces

            path = request.subpath[i] if i < len(request.subpath) else None
            result = match(pattern, path, flags)
            if not result:
                return False
            if name and path is not None:
                named_subpaths[name] = path
            if hasattr(result, "groupdict"):
                named_subpaths.update(result.groupdict())

        request.named_subpaths = named_subpaths
        return True


def asdict(setting: str | dict | None, value_type: Callable = lambda x: x) -> dict:
    """Parse a multi-line 'key = value' setting into a dict.

    A dict is returned unchanged; None becomes an empty dict. Values run through
    value_type.
    """
    if setting is None:
        return {}
    if isinstance(setting, dict):
        return setting
    result = {}
    for line in setting.splitlines():
        line = line.strip()
        if not line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value_type(value.strip())
    return result


def includeme(config: Configurator) -> None:
    """Add the ``param`` request method and the ``subpath`` view predicate."""
    config.add_request_method(param, name="param")
    config.add_view_predicate("subpath", SubpathPredicate)

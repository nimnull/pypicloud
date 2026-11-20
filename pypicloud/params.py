"""Validation of request parameters against view annotations."""

import functools
import inspect
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError, create_model
from pyramid.httpexceptions import HTTPBadRequest

from .duh import is_request

# Arguments supplied by pyramid, never read from the request
VIEW_ARGS = frozenset({"self", "context", "request"})


def _model_for(fxn: Callable) -> type:
    """Build the pydantic model that validates the parameters of one view."""
    fields: dict[str, Any] = {}
    for name, arg in inspect.signature(fxn).parameters.items():
        if name in VIEW_ARGS:
            continue
        annotation = Any if arg.annotation is arg.empty else arg.annotation
        default = ... if arg.default is arg.empty else arg.default
        fields[name] = (annotation, default)
    return create_model(
        f"{fxn.__name__.title().replace('_', '')}Params",
        __config__={
            "arbitrary_types_allowed": True,
            "extra": "ignore",
            "populate_by_name": True,
        },
        **fields,
    )


def _view_args(args: tuple) -> dict[str, Any]:
    """The view arguments pyramid supplied, by name.

    A function view is called with the context and the request, or with the request
    alone; a view class method is called with the instance, which carries both.
    """
    if len(args) == 2 and is_request(args[1]):
        return {"context": args[0], "request": args[1]}
    if len(args) == 1 and is_request(args[0]):
        return {"context": getattr(args[0], "context", None), "request": args[0]}
    if len(args) == 1:
        view = args[0]
        return {
            "self": view,
            "context": getattr(view, "context", None),
            "request": view.request,
        }
    raise TypeError(f"Cannot find the request in view arguments {args!r}")


def _request_data(request: Any) -> dict[str, Any]:
    """Parameters of a request: the JSON body if it has one, else GET/POST params."""
    if getattr(request, "content_type", "") == "application/json":
        body = request.json_body
        if not isinstance(body, dict):
            raise HTTPBadRequest("Request body must be a JSON object")
        return body
    return dict(request.params)


def use_params(fxn: Callable) -> Callable:
    """Validate request parameters and pass them to the view as keyword arguments.

    Every parameter of the view other than ``self``, ``context`` and ``request`` is
    read from the request and coerced to its annotation; an unannotated parameter is
    passed through untouched. A parameter with no default is required, and unknown
    parameters in the request are ignored.

    Raises HTTPBadRequest naming every parameter that is missing or malformed. Keyword
    arguments passed by the caller override what the request carries, so views stay
    directly callable in tests.
    """
    model = _model_for(fxn)
    declared = frozenset(inspect.signature(fxn).parameters) & VIEW_ARGS

    @functools.wraps(fxn)
    def validate_params(*args: Any, **kwargs: Any) -> Any:
        view_args = _view_args(args)
        try:
            params = model(**(_request_data(view_args["request"]) | kwargs))
        except ValidationError as e:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in e.errors()
            )
            raise HTTPBadRequest(detail) from e
        scope = {name: view_args[name] for name in declared}
        return fxn(**scope, **dict(params))

    return validate_params

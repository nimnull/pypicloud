"""Tests for request parameter validation"""

import unittest
from typing import Annotated, Any

from pydantic import Field
from pyramid.httpexceptions import HTTPBadRequest
from pyramid.testing import DummyRequest

from pypicloud.params import use_params


@use_params
def sample_view(request, name: str, count: int = 1, flag: bool = False):
    """Return the parameters the view was called with"""
    return {"name": name, "count": count, "flag": flag}


@use_params
def aliased_view(request, action: Annotated[str, Field(alias=":action")] = "upload"):
    return action


@use_params
def file_view(request, content: Any):
    return content


class ViewClass:
    def __init__(self, request):
        self.request = request

    @use_params
    def method(self, admin: bool):
        return admin


class TestUseParams(unittest.TestCase):
    """Tests for the use_params decorator"""

    def _request(self, params=None, json_body=None):
        request = DummyRequest(params=params or {})
        if json_body is not None:
            request.content_type = "application/json"
            request.json_body = json_body
        else:
            request.content_type = ""
        return request

    def test_reads_params(self):
        """Parameters come off the request"""
        ret = sample_view(self._request({"name": "pkg"}))
        self.assertEqual(ret, {"name": "pkg", "count": 1, "flag": False})

    def test_coerces_strings(self):
        """Form and query values are coerced to the annotated type"""
        ret = sample_view(self._request({"name": "pkg", "count": "3", "flag": "true"}))
        self.assertEqual(ret, {"name": "pkg", "count": 3, "flag": True})

    def test_reads_json_body(self):
        """A JSON request body supplies the parameters"""
        ret = sample_view(self._request(json_body={"name": "pkg", "count": 2}))
        self.assertEqual(ret["count"], 2)

    def test_missing_required(self):
        """A missing required parameter is a 400 that names the parameter"""
        with self.assertRaises(HTTPBadRequest) as ctx:
            sample_view(self._request())
        self.assertIn("name", str(ctx.exception))

    def test_malformed_value(self):
        """A value that doesn't fit the annotation is a 400"""
        with self.assertRaises(HTTPBadRequest) as ctx:
            sample_view(self._request({"name": "pkg", "count": "abc"}))
        self.assertIn("count", str(ctx.exception))

    def test_ignores_unknown_params(self):
        """Parameters the view doesn't declare are ignored"""
        ret = sample_view(self._request({"name": "pkg", "md5_digest": "abc"}))
        self.assertEqual(ret["name"], "pkg")

    def test_keyword_arguments_win(self):
        """Keyword arguments from the caller override the request"""
        ret = sample_view(self._request({"name": "pkg"}), name="other")
        self.assertEqual(ret["name"], "other")

    def test_context_and_request(self):
        """A view called with (context, request) reads the request"""
        ret = sample_view(object(), self._request({"name": "pkg"}))
        self.assertEqual(ret["name"], "pkg")

    def test_view_class_method(self):
        """A view class method reads the request off the instance"""
        self.assertIs(ViewClass(self._request({"admin": "yes"})).method(), True)

    def test_alias(self):
        """A parameter can be read from a differently named request field"""
        self.assertEqual(aliased_view(self._request({":action": "remove"})), "remove")
        self.assertEqual(aliased_view(self._request()), "upload")

    def test_unvalidated_value(self):
        """An Any parameter passes through untouched"""
        upload = object()
        self.assertIs(file_view(self._request({"content": upload})), upload)

    def test_json_body_must_be_object(self):
        """A JSON body that isn't an object is a 400"""
        with self.assertRaises(HTTPBadRequest):
            sample_view(self._request(json_body=["pkg"]))

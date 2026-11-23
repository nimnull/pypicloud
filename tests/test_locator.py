"""Tests for the upstream index locators"""

import unittest
from unittest.mock import MagicMock, patch

import requests

from pypicloud.locator import SimpleJsonLocator

RELEASE_JSON = {
    "info": {"summary": "A package summary"},
    "releases": {
        "1.0": [
            {
                "url": "https://pypi.org/mypkg/mypkg-1.0.tar.gz",
                "digests": {"sha256": "abc"},
                "requires_python": ">=3.14",
            }
        ]
    },
}


class TestSimpleJsonLocator(unittest.TestCase):
    """Tests for SimpleJsonLocator"""

    def setUp(self):
        self.locator = SimpleJsonLocator("https://pypi.org")

    @patch("pypicloud.locator.requests.get")
    def test_fetch_releases(self, get):
        """Releases are read out of the upstream JSON document"""
        get.return_value = MagicMock(**{"json.return_value": RELEASE_JSON})
        releases = self.locator.get_releases("mypkg")
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]["version"], "1.0")
        self.assertEqual(releases[0]["summary"], "A package summary")
        self.assertEqual(releases[0]["requires_python"], ">=3.14")

    @patch("pypicloud.locator.requests.get")
    def test_success_is_cached(self, get):
        """A successful fetch is served from the cache on the next read"""
        get.return_value = MagicMock(**{"json.return_value": RELEASE_JSON})
        self.locator.get_releases("mypkg")
        self.locator.get_releases("mypkg")
        self.assertEqual(get.call_count, 1)

    @patch("pypicloud.locator.requests.get")
    def test_failure_is_not_cached(self, get):
        """A failed fetch reads as empty and is retried on the next read"""
        get.side_effect = requests.RequestException("upstream is down")
        self.assertEqual(self.locator.get_releases("mypkg"), [])
        self.assertEqual(self.locator.get_releases("mypkg"), [])
        self.assertEqual(get.call_count, 2)

    @patch("pypicloud.locator.requests.get")
    def test_cache_is_bounded(self, get):
        """The cache evicts the oldest name instead of growing without limit"""
        get.return_value = MagicMock(**{"json.return_value": RELEASE_JSON})
        max_size = self.locator._cache._max_size
        for i in range(max_size + 10):
            self.locator.get_releases(f"mypkg{i}")
        self.assertEqual(len(self.locator._cache), max_size)

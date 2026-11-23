"""Simple replacement for distlib SimpleScrapingLocator"""

import logging

import distlib.locators
import requests
from distlib.locators import SimpleScrapingLocator

from .util import TimedCache

LOG = logging.getLogger(__name__)


class SimpleJsonLocator:
    """Simple replacement for distlib SimpleScrapingLocator"""

    def __init__(self, base_index):
        self.base_index = base_index
        # 16m cache, keyed on a name the client chooses, so it is bounded
        self._cache = TimedCache(1000, self._get_releases, max_size=1000)

    def get_releases(self, project_name):
        """The upstream releases of a project, or an empty list when it has none.

        A failed upstream request also reads as an empty list, and is not cached.
        """
        return self._cache.get(project_name, [])

    def _get_releases(self, project_name):
        url = f"{self.base_index}/pypi/{project_name}/json"
        try:
            response = requests.get(url)
            response.raise_for_status()
        except requests.RequestException as e:
            LOG.warning("Error fetching '%s' from upstream: %s", project_name, e)
            return None
        data = response.json()
        items = []
        summary = data["info"].get("summary")
        for version, releases in data["releases"].items():
            for release in releases:
                try:
                    item = {
                        "name": project_name,
                        "version": version,
                        "summary": summary,
                        "url": release["url"],
                        "digests": release.get("digests", {}),
                        "requires_python": release["requires_python"],
                    }
                except KeyError:
                    continue
                items.append(item)
        return items


class FormattedScrapingLocator(SimpleScrapingLocator):
    def get_releases(self, project_name):
        projects = self.get_project(project_name)
        items = []
        for version, urls in projects["urls"].items():
            for url in urls:
                dist = projects[version]
                digest = projects["digests"].get(url)
                digest_dict = {}
                if digest is not None:
                    digest_dict[digest[0]] = digest[1]
                items.append(
                    {
                        "name": dist.name,
                        "version": dist.version,
                        "summary": dist.metadata.dictionary.get("summary"),
                        "url": url,
                        "digests": digest_dict,
                        "requires_python": dist.metadata.dictionary.get(
                            "requires_python"
                        ),
                    }
                )
        return items


# Distlib checks if wheels are compatible before returning them.
# This is useful if you are attempting to install on the system running
# distlib, but we actually want ALL wheels so we can display them to the
# clients.  So we have to monkey patch the method. I'm sorry.
def is_compatible(wheel, tags=None):
    """Hacked function to monkey patch into distlib"""
    return True


distlib.locators.is_compatible = is_compatible

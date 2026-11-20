"""Store packages as files on disk"""

import contextlib
import json
import os
from binascii import hexlify
from datetime import UTC, datetime
from pathlib import Path
from typing import override

from pyramid.response import FileResponse

from pypicloud.models import Package
from pypicloud.util import stream_file

from .base import IStorage


class FileStorage(IStorage):
    """Stores package files on the filesystem"""

    def __init__(self, request=None, **kwargs):
        self.directory = Path(kwargs.pop("directory"))
        super().__init__(request, **kwargs)

    @classmethod
    @override
    def configure(cls, settings):
        kwargs = super().configure(settings)
        directory = Path(settings["storage.dir"]).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        kwargs["directory"] = directory
        return kwargs

    def get_path(self, package) -> Path:
        """Get the fully-qualified file path for a package"""
        return self.directory / package.name / package.version / package.filename

    def path_to_meta_path(self, path: Path) -> Path:
        """Construct the path of the metadata file that goes with a package file"""
        return path.with_name(path.name + ".meta")

    def get_metadata_path(self, package) -> Path:
        """Get the fully-qualified file path for a package metadata file"""
        return self.path_to_meta_path(self.get_path(package))

    @override
    def list(self, factory=Package):
        for root, _, files in self.directory.walk():
            for filename in files:
                if filename.endswith(".meta"):
                    # We don't want to yield for this file
                    continue

                name, version = root.relative_to(self.directory).parts
                fullpath = root / filename
                last_modified = datetime.fromtimestamp(fullpath.stat().st_mtime, UTC)
                metadata = {}
                metafile = self.path_to_meta_path(fullpath)
                if metafile.exists():
                    # If JSON fails to decode, don't sweat it.
                    with (
                        contextlib.suppress(ValueError),
                        metafile.open(encoding="utf-8") as mfile,
                    ):
                        metadata = json.load(mfile)
                yield factory(name, version, filename, last_modified, **metadata)

    @override
    def download_response(self, package):
        return FileResponse(
            self.get_path(package),
            request=self.request,
            content_type="application/octet-stream",
        )

    @override
    def upload(self, package, datastream):
        destfile = self.get_path(package)
        destdir = destfile.parent
        destdir.mkdir(parents=True, exist_ok=True)
        uid = hexlify(os.urandom(4)).decode("utf-8")

        # Store metadata as JSON. This could be expanded in the future
        # to store additional metadata about a package (i.e. author)
        temp = destdir / f".metadata.{uid}"
        with temp.open("w", encoding="utf-8") as mfile:
            json.dump(package.get_metadata(), mfile)
        temp.replace(self.get_metadata_path(package))

        # Write to a temporary file
        temp = destdir / f".{package.filename}.{uid}"
        with temp.open("wb") as ofile:
            ofile.writelines(stream_file(datastream))
        temp.replace(destfile)

    @override
    def delete(self, package):
        path = self.get_path(package)
        path.unlink()
        # Metadata file may not exist
        self.get_metadata_path(package).unlink(missing_ok=True)
        version_dir = path.parent
        try:
            version_dir.rmdir()
            version_dir.parent.rmdir()
        except OSError:
            return

    @override
    def open(self, package):
        return self.get_path(package).open("rb")

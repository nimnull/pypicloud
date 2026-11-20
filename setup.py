"""Setup file"""

import re
from pathlib import Path

from setuptools import find_packages, setup

HERE = Path(__file__).resolve().parent
README = (HERE / "README.rst").read_text(encoding="utf-8")
CHANGES = (HERE / "CHANGES.rst").read_text(encoding="utf-8")
# Remove custom RST extensions for pypi
CHANGES = re.sub(r"\(\s*:(issue|pr|sha):.*?\)", "", CHANGES)
CHANGES = re.sub(r":ref:`(.*?) <.*>`", r"\1", CHANGES)

REQUIREMENTS = [
    "smart_open[s3,http]>=7.0",
    "boto3>=1.35",
    # beaker needs this
    "cryptography",
    "distlib",
    "packaging>=23",
    "pydantic>=2.9",
    "paste>=3.10",
    "passlib>=1.7",
    "pyramid>=2.1",
    "pyramid_beaker",
    "pyramid_jinja2",
    "pyramid_rpc",
    "pyramid_tm",
    "requests",
    "transaction",
    "zope.sqlalchemy",
]

EXTRAS = {
    "ldap": ["python-ldap"],
    "dynamo": ["flywheel>=0.2.0"],
    "redis": ["redis"],
    "gcs": [
        "google-cloud-storage>=1.10.0",
        "smart_open[gcs]>=6.3.0",  # ref #320
    ],
    "azure-blob": [
        "azure-storage-blob>=12.5.0",
        "smart_open[azure]>=6.1.0",  # ref #304
    ],
}

EXTRAS["all_plugins"] = sum(EXTRAS.values(), [])

EXTRAS["server"] = ["waitress"]


if __name__ == "__main__":
    setup(
        name="pypicloud",
        version="1.3.12",
        description="Private PyPI backed by S3",
        long_description=README + "\n\n" + CHANGES,
        classifiers=[
            "Programming Language :: Python",
            "Programming Language :: Python :: 3",
            "Programming Language :: Python :: 3.14",
            "Programming Language :: Python :: 3 :: Only",
            "Development Status :: 4 - Beta",
            "Framework :: Pyramid",
            "Intended Audience :: System Administrators",
            "Intended Audience :: Developers",
            "License :: OSI Approved :: MIT License",
            "Topic :: Internet :: WWW/HTTP",
            "Topic :: System :: Systems Administration",
        ],
        license="MIT",
        author="Steven Arcangeli",
        author_email="stevearc@stevearc.com",
        url="http://pypicloud.readthedocs.org/",
        keywords="pypi s3 cheeseshop package",
        platforms="any",
        zip_safe=False,
        python_requires=">=3.14",
        include_package_data=True,
        packages=find_packages(exclude=("tests",)),
        entry_points={
            "console_scripts": [
                "pypicloud-gen-password = pypicloud.scripts:gen_password",
                "pypicloud-make-config = pypicloud.scripts:make_config",
                "ppc-gen-password = pypicloud.scripts:gen_password",
                "ppc-make-config = pypicloud.scripts:make_config",
                "ppc-migrate = pypicloud.scripts:migrate_packages",
                "ppc-export = pypicloud.scripts:export_access",
                "ppc-import = pypicloud.scripts:import_access",
                "ppc-create-s3-sync = pypicloud.lambda_scripts:create_sync_scripts",
                "ppc-build-lambda-bundle = pypicloud.lambda_scripts:build_lambda_bundle",
            ],
            "paste.app_factory": ["main = pypicloud:main"],
        },
        install_requires=REQUIREMENTS,
        extras_require=EXTRAS,
    )

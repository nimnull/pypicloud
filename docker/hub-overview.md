# pypicloud

Private PyPI server. Packages live in S3, Google Cloud Storage, Azure Blob Storage
or on disk; the package index, permissions and web UI come from this server.

This is a fork of [stevearc/pypicloud](https://github.com/stevearc/pypicloud)
modernized for Python 3.14. Source and issues:
[nimnull/pypicloud](https://github.com/nimnull/pypicloud).

## Tags

| Tag | Platforms |
|---|---|
| `2.0.0a1`, `latest` | `linux/amd64`, `linux/arm64` |

`latest` currently points at an alpha. Read *Upgrading from 1.x* before using it
with an existing deployment.

## Quick start

```bash
# Write a config file (prompts for storage, cache and auth backends)
pip install pypicloud && ppc-make-config -r config.ini

docker run -p 8080:8080 \
    -v $PWD/config.ini:/etc/pypicloud/config.ini:ro \
    nimnull/pypicloud
```

The web interface is then on <http://localhost:8080/>, and pip installs from
`http://localhost:8080/simple/`.

## What is in the image

- CPython 3.14 and uWSGI 2.0.31, listening on port 8080
- Every backend below is installed; nothing extra to add at runtime
- Runs as the unprivileged user `pypicloud` (uid 1000), and drops privileges
  itself when the container is started as root
- Writable data directory at `/var/lib/pypicloud`

## Supported options

| Setting | Values |
|---|---|
| `pypi.storage` | `file`, `s3`, `cloudfront`, `gcs`, `azure-blob` |
| `pypi.db` | `sql` (SQLAlchemy 2.x: SQLite, Postgres, MySQL), `redis`, `dynamo` |
| `pypi.auth` | `config`, `sql`, `ldap`, `remote`, `aws_secrets_manager` |
| `auth.scheme` | `argon2` (default, argon2id), `bcrypt` |
| `pypi.fallback` | `redirect`, `cache`, `none` |

Full reference: <https://pypicloud.readthedocs.io/>.

## Providing configuration

**A config file**, mounted at `/etc/pypicloud/config.ini`:

```bash
docker run -p 8080:8080 -v $PWD/config.ini:/etc/pypicloud/config.ini:ro nimnull/pypicloud
```

**Environment variables**, which override the file. Any setting becomes
`PPC_` + the setting name uppercased with dots turned into underscores:

| Setting | Variable |
|---|---|
| `pypi.storage` | `PPC_PYPI_STORAGE` |
| `storage.dir` | `PPC_STORAGE_DIR` |
| `storage.bucket` | `PPC_STORAGE_BUCKET` |
| `db.url` | `PPC_DB_URL` |
| `session.secure` | `PPC_SESSION_SECURE` |

```bash
docker run -p 8080:8080 \
    -v $PWD/config.ini:/etc/pypicloud/config.ini:ro \
    -e PPC_PYPI_STORAGE=s3 \
    -e PPC_STORAGE_BUCKET=my-packages \
    -e PPC_DB_URL=postgresql://user:pass@postgres/pypicloud \
    nimnull/pypicloud
```

**A volume** for the file storage backend and for a SQLite cache:

```bash
docker run -p 8080:8080 \
    -v $PWD/config.ini:/etc/pypicloud/config.ini:ro \
    -v pypicloud-data:/var/lib/pypicloud \
    nimnull/pypicloud
```

Put TLS in front of it with a reverse proxy, and set `session.secure = true`.

## Upgrading from 1.x

This release breaks three things:

1. **Python 3.14 only.**
2. **Every stored password has to be set again.** Passwords are hashed with
   argon2id through pwdlib; the passlib hashes written by 1.x do not verify. A
   login against one is refused and logged.
3. **`pip search` is gone**, along with the XML-RPC endpoints at `/pypi`. PyPI
   disabled its own search API in 2021.

The storage backends, the SQL schema and the DynamoDB tables are unchanged.

Full notes:
[CHANGES.rst](https://github.com/nimnull/pypicloud/blob/master/CHANGES.rst).

# Production image: pypicloud served by uWSGI
FROM python:3.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libldap2-dev \
        libpcre3-dev \
        libsasl2-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /src
COPY pyproject.toml uv.lock README.rst ./
COPY pypicloud ./pypicloud
# uwsgi has no wheels, so it is compiled here and copied into the runtime image
RUN uv sync --frozen --no-dev --no-editable --extra ldap --extra redis --extra dynamo \
    && uv pip install --no-cache --python /opt/venv/bin/python uwsgi

FROM python:3.14-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libldap-2.5-0 \
        libpcre3 \
        libsasl2-2 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 1000 -ms /bin/bash pypicloud \
    && mkdir -p /var/lib/pypicloud \
    && chown pypicloud:pypicloud /var/lib/pypicloud

COPY --from=builder --chown=root:root /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

USER pypicloud
WORKDIR /var/lib/pypicloud
EXPOSE 8080

# Generate the config with: ppc-make-config -r config.ini
CMD ["uwsgi", "--master", "--enable-threads", "--http", "0.0.0.0:8080", \
     "--paste", "config:/etc/pypicloud/config.ini"]

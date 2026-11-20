FROM python:3.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        git \
        libldap2-dev \
        libsasl2-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# The container runs as the host user, so the venv lives in the mounted tree
ENV UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache

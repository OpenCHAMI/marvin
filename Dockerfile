FROM ghcr.io/astral-sh/uv:python3.12-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/root/.local/bin:$PATH \
    VIRTUAL_ENV=/opt/venv \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        gcc \
        git \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/marvin

COPY pyproject.toml uv.lock README.md marvin.py ./
COPY src ./src

ENV PATH="$VIRTUAL_ENV/bin:/root/.local/bin:$PATH"

RUN uv sync --frozen --no-dev --no-editable

FROM golang:1.25-bookworm AS go-tooling

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/usr/local/go/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        libxml2 \
        libxslt1.1 \
        make \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/
COPY --from=builder /opt/venv /opt/venv
COPY --from=go-tooling /usr/local/go /usr/local/go

WORKDIR /workspace
VOLUME ["/workspace"]

ENTRYPOINT ["marvin"]
CMD ["--help"]
# Reproducible simulated-robot CI environment (no physical Reachy, no portal NATS).
# Slim install: avoids reachy-mini SDK and PyGObject/GStreamer system deps.

FROM python:3.12-slim-bookworm

WORKDIR /workspace

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts

RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -e ".[ci]"

ENV PYTHONPATH=src

RUN python -m unittest discover -s tests -v \
    && python tests/smoke_sim_runtime.py

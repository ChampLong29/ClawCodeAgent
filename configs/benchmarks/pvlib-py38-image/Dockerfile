FROM python:3.8-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade "pip<25" && \
    python -m pip install -r /tmp/requirements.txt

WORKDIR /workspace

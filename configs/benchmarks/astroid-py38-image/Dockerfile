ARG BASE_IMAGE=claw/dsh-builder@sha256:4e1e29acfc317235f5698c41dff4dd21ab5f5c772080a28b000c44fe7747df78
FROM ${BASE_IMAGE}

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/claw/src

COPY cpython-3.8.20+20241002-x86_64-unknown-linux-gnu-install_only.tar.gz /tmp/python38.tar.gz
RUN mkdir -p /opt/task && \
    tar -xzf /tmp/python38.tar.gz -C /opt/task --strip-components=1 && \
    rm /tmp/python38.tar.gz

COPY requirements.txt /tmp/task-requirements.txt
COPY wheelhouse /tmp/wheelhouse
RUN /opt/task/bin/python3.8 -m pip install \
      --no-index \
      --find-links=/tmp/wheelhouse \
      --require-hashes \
      -r /tmp/task-requirements.txt && \
    rm -rf /tmp/wheelhouse /tmp/task-requirements.txt

COPY claw-src/claw /opt/claw/src/claw

WORKDIR /workspace

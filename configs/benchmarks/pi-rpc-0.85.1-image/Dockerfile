ARG BASE_IMAGE=claw/dsh-builder@sha256:4e1e29acfc317235f5698c41dff4dd21ab5f5c772080a28b000c44fe7747df78
FROM ${BASE_IMAGE}

COPY pi-runtime-0.85.1.tar.gz /tmp/pi-runtime.tar.gz
RUN mkdir -p /opt/pi && \
    tar -xzf /tmp/pi-runtime.tar.gz -C /opt/pi && \
    rm /tmp/pi-runtime.tar.gz && \
    test "$(node -p "require('/opt/pi/node_modules/@earendil-works/pi-coding-agent/package.json').version")" = "0.85.1"

WORKDIR /workspace

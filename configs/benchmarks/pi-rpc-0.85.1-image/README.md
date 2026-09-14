# Pi RPC 0.85.1 image

This directory freezes the Pi RPC baseline used by the Docker-host three-arm
pilot. The package moved from the `@mariozechner` namespace; the reproducible
pin is `@earendil-works/pi-coding-agent@0.85.1`. `package-lock.json` pins the
npm dependency closure at SHA-256
`c8b7c7fab6d8d97df83c9b69bb029d30553eda115584d60d4318f9e2912359ab`.

The local offline build context also requires the ignored archive
`pi-runtime-0.85.1.tar.gz`, verified at SHA-256
`3f47449f7af12dc7ddb994f5bc29fddc1e975e5b86d3b2dd278736acfc0f1f06`.
It is copied into `/opt/pi`; Pi is available at
`/opt/pi/node_modules/.bin/pi`. The base image supplies Node 24, satisfying
Pi's declared Node `>=22.19.0` requirement.

The admitted 2026-09-14 local build is:

```text
claw-local/pi-rpc@sha256:cd01a10974964bc6179e532a9d821dbf22074b3ecb5051a15680180f261c3e69
```

On another machine, install the exact package from `package-lock.json` with
Node 24, archive the runtime under the expected name, and build from a locally
verified base with implicit pulls disabled. Record the newly built image digest
before freezing or running Episodes; cross-machine rebuilds are not assumed to
retain this host's digest.

The complete Pi process runs in the container. Provider access therefore makes
networking technically available to Pi's Shell tool, while Claw's Shell sandbox
remains offline. Reports must retain this treatment difference and must not
claim network-policy parity.

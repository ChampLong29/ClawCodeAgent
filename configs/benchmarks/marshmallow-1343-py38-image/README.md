# Marshmallow-1343 task image

This image separates the current Claw evaluator runtime from the historical
task runtime:

- `/usr/bin/python3`: Claw evaluator on Python 3.11;
- `/opt/task/bin/python3.8`: Marshmallow tests on CPython 3.8.20.

The CPython install-only archive is
`cpython-3.8.20+20241002-x86_64-unknown-linux-gnu-install_only.tar.gz`, SHA-256
`285e141c36f88b2e9357654c5f77d1f8fb29cc25132698fe35bb30d787f38e87`.
`requirements.txt` pins every task wheel by hash. Claw source is copied from the
same Git checkout used to generate the Episode and the final image must be
addressed through a locally inspected digest; the Docker backend never pulls a
missing image implicitly.

The admitted 2026-09-13 local build is:

```text
claw-local/sweb-marshmallow1343@sha256:8090971e2cf3ce9e6e7911afc17e58270a939708992996d9c905674ae28fa52d
```

This is local Dev calibration infrastructure, not an official SWE-bench image
or score.

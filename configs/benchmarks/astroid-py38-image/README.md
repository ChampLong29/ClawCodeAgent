# Astroid Python 3.8 task image

This image separates the current Claw evaluator runtime from the historical
task runtime:

- `/usr/bin/python3`: Claw evaluator on Python 3.11;
- `/opt/task/bin/python3.8`: Astroid tests on CPython 3.8.20.

The CPython install-only archive is
`cpython-3.8.20+20241002-x86_64-unknown-linux-gnu-install_only.tar.gz`, SHA-256
`285e141c36f88b2e9357654c5f77d1f8fb29cc25132698fe35bb30d787f38e87`.
`requirements.txt` pins every task wheel by hash. Claw source is copied from the
same Git checkout used to generate the Episode. Build output must be inspected
and addressed through its local digest; the Docker backend never pulls a
missing image implicitly.

The admitted 2026-09-14 local build is:

```text
claw-local/sweb-astroid@sha256:4ad0ddae25284242edbd6cbc27f6555f96540164090edc883e9acb29486747e6
```

Both frozen Astroid tasks (`pylint-dev__astroid-1196` and
`pylint-dev__astroid-1333`) passed their baseline-to-Oracle transition inside
the restricted container. This is local Dev calibration infrastructure, not an
official SWE-bench image or score.

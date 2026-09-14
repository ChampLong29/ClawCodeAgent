# SQLFluff-1763 Python 3.8 task image

This image separates the current Claw evaluator (`/usr/bin/python3`) from the
historical task runtime (`/opt/task/bin/python3.8`). The task interpreter is
CPython 3.8.20 from the install-only archive with SHA-256
`285e141c36f88b2e9357654c5f77d1f8fb29cc25132698fe35bb30d787f38e87`.

All third-party wheels are pinned by hash. The base-commit SQLFluff source is
installed non-editably to supply the package's plugin entry-point metadata;
during evaluation the isolated Episode source takes precedence on
`PYTHONPATH`. Claw source is copied from the checkout that constructs the
Episode. The deterministic Git archive for base commit
`a10057635e5b2559293a676486f0b730981f037a` has SHA-256
`90380898505ef6a5ac3002f58349dedfafd4c9ded44f6e8c43c8a931d544d41a`.

The admitted 2026-09-14 local build is:

```text
claw-local/sweb-sqlfluff1763@sha256:cd779d42b6fe9fd7808f5245db4c30bfdaa03b935268f07d4313d30ba1ea4a50
```

The frozen task's baseline-to-Oracle transition passed in the restricted
container. This is local Dev calibration infrastructure, not an official
SWE-bench image or score.

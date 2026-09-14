# pydicom Python 3.8 task image

This image separates the current Claw evaluator (`/usr/bin/python3`) from the
historical task runtime (`/opt/task/bin/python3.8`). The task interpreter is
CPython 3.8.20 from the install-only archive with SHA-256
`285e141c36f88b2e9357654c5f77d1f8fb29cc25132698fe35bb30d787f38e87`.

The dependency lock intentionally retains pytest 5.4.3. The selected historical
PASS_TO_PASS tests use xUnit hooks whose behavior is incompatible with pytest 8;
changing those tests would invalidate the calibration. Every wheel is pinned by
hash, and Claw source is copied from the checkout that constructs the Episode.

The admitted 2026-09-14 local build is:

```text
claw-local/sweb-pydicom@sha256:7b75f81a55dfc77d0e9ee1bcc61b4747cadd1ee07dd89010ef6bfa58d8e20351
```

Both frozen pydicom tasks (`pydicom__pydicom-1139` and
`pydicom__pydicom-1413`) passed their baseline-to-Oracle transitions in the
restricted container. This is local Dev calibration infrastructure, not an
official SWE-bench image or score.

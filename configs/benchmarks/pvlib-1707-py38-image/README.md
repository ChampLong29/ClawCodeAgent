# pvlib-1707 Python 3.8 task image

This image separates the current Claw evaluator (`/usr/bin/python3`) from the
historical task runtime (`/opt/task/bin/python3.8`). The task interpreter is
CPython 3.8.20 from the install-only archive with SHA-256
`285e141c36f88b2e9357654c5f77d1f8fb29cc25132698fe35bb30d787f38e87`.

The numerical stack is frozen at NumPy 1.24.4, pandas 1.5.3 and SciPy 1.10.1.
pytest-mock 3.14.0 is included because the selected historical regression set
requires its `mocker` fixture. Every wheel is pinned by hash, and Claw source is
copied from the checkout that constructs the Episode.

The admitted 2026-09-14 local build is:

```text
claw-local/sweb-pvlib1707@sha256:86822a97f04f0f0900921a8fac1750abb8f8ad45bd0b7dec088f6585a9b16f33
```

The frozen task's baseline-to-Oracle transition passed in the restricted
container. This is local Dev calibration infrastructure, not an official
SWE-bench image or score.

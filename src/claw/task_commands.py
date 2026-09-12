"""Portable resolution for trusted, versioned task-suite commands."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Dict, Iterable, Mapping, Optional


SANDBOX_INHERITED_ENV_NAMES = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TZ",
)

_SANDBOX_MANAGED_ENV_NAMES = {
    "HOME",
    "PATH",
    "TMPDIR",
}

_SANDBOX_UNSAFE_ENV_NAMES = {
    "BASH_ENV",
    "CDPATH",
    "ENV",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "PYTHONHOME",
    "PYTHONPATH",
}

_SENSITIVE_ENV_FRAGMENTS = (
    "API_KEY",
    "AUTH_TOKEN",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
)

_SENSITIVE_ENV_SUFFIXES = (
    "_KEY",
    "_PASSWORD",
    "_SECRET",
    "_TOKEN",
)

_SENSITIVE_ENV_NAMES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "SSH_AUTH_SOCK",
}

_DYNAMIC_LOADER_ENV_PREFIXES = (
    "DYLD_",
    "LD_",
)


def runtime_subprocess_environment() -> Dict[str, str]:
    """Return the ambient environment with Claw's interpreter directory first."""
    environment = os.environ.copy()
    runtime_bin = os.path.dirname(os.path.abspath(sys.executable))
    current_path = environment.get("PATH", "")
    path_entries = [entry for entry in current_path.split(os.pathsep) if entry]
    normalized = {os.path.normcase(os.path.abspath(entry)) for entry in path_entries}
    if os.path.normcase(os.path.abspath(runtime_bin)) not in normalized:
        path_entries.insert(0, runtime_bin)
    environment["PATH"] = os.pathsep.join(path_entries)
    return environment


def is_sensitive_environment_name(name: str) -> bool:
    """Return whether *name* must not cross into untrusted execution."""
    normalized = str(name).strip().upper()
    if normalized in _SENSITIVE_ENV_NAMES or normalized in _SANDBOX_UNSAFE_ENV_NAMES:
        return True
    if normalized.startswith(_DYNAMIC_LOADER_ENV_PREFIXES):
        return True
    if normalized.endswith(_SENSITIVE_ENV_SUFFIXES):
        return True
    return any(fragment in normalized for fragment in _SENSITIVE_ENV_FRAGMENTS)


def sandbox_subprocess_environment(
    *,
    cwd: Optional[str] = None,
    inherited_names: Optional[Iterable[str]] = None,
    values: Optional[Mapping[str, str]] = None,
    managed_path: Optional[str] = None,
    managed_home: Optional[str] = None,
    managed_tmpdir: Optional[str] = None,
) -> Dict[str, str]:
    """Build a minimal environment for model-directed process execution.

    Unlike :func:`runtime_subprocess_environment`, this function never copies
    the ambient environment wholesale.  A small non-secret allowlist may be
    inherited, while explicit values are rejected when their names look like
    credentials or dynamic-loader controls.
    """
    environment: Dict[str, str] = {}
    selected_names = (
        SANDBOX_INHERITED_ENV_NAMES
        if inherited_names is None
        else inherited_names
    )
    for raw_name in selected_names:
        name = str(raw_name)
        if is_sensitive_environment_name(name):
            continue
        if name in os.environ:
            environment[name] = os.environ[name]

    if managed_path is None:
        runtime_bin = os.path.dirname(os.path.abspath(sys.executable))
        safe_path_candidates = (
            runtime_bin,
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        )
        path_entries = []
        normalized = set()
        for entry in safe_path_candidates:
            normalized_entry = os.path.normcase(os.path.abspath(entry))
            if os.path.isdir(entry) and normalized_entry not in normalized:
                path_entries.append(entry)
                normalized.add(normalized_entry)
        if not path_entries:
            path_entries = [runtime_bin]
        managed_path = os.pathsep.join(path_entries)

    execution_root = os.path.realpath(cwd) if cwd else tempfile.gettempdir()
    environment["PATH"] = managed_path
    environment["HOME"] = managed_home or execution_root
    environment["TMPDIR"] = managed_tmpdir or tempfile.gettempdir()

    for raw_name, raw_value in (values or {}).items():
        name = str(raw_name)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ValueError(f"Invalid sandbox environment name: {name!r}")
        if name.upper() in _SANDBOX_MANAGED_ENV_NAMES:
            raise ValueError(
                f"Sandbox environment variable is backend-managed: {name}"
            )
        if is_sensitive_environment_name(name):
            raise ValueError(
                f"Sensitive environment variable is not allowed in sandbox execution: {name}"
            )
        value = str(raw_value)
        if "\x00" in value:
            raise ValueError(
                f"Sandbox environment variable contains a null byte: {name}"
            )
        environment[name] = value

    return environment


def resolve_task_command(
    command: str,
    *,
    python_executable: Optional[str] = None,
) -> str:
    """Bind a leading bare ``python`` marker to Claw's interpreter.

    Only the first command token is replaced. Shell operators and later tokens
    remain untouched, preserving the trusted manifest command's semantics.
    """
    match = re.match(r"^(\s*)python(?=\s|$)", command)
    if match is None:
        return command
    executable_path = str(python_executable or sys.executable)
    executable = (
        subprocess.list2cmdline([executable_path])
        if os.name == "nt"
        else shlex.quote(executable_path)
    )
    return f"{match.group(1)}{executable}{command[match.end():]}"

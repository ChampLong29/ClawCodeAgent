"""Bash command security validation.

This module provides comprehensive security validation for bash commands
to prevent command injection, dangerous operations, and other security risks.
"""

from __future__ import annotations

import os
import re
import subprocess
from enum import Enum
from typing import List, Optional, Tuple


class SecurityResult(Enum):
    """Security validation result types."""
    ALLOW = "allow"           # Command is safe, execute directly
    ASK = "ask"               # Command requires user confirmation
    DENY = "deny"             # Command is dangerous, block execution
    PASSTHROUGH = "passthrough"  # Execute with warning


class SecurityLevel(Enum):
    """Security level for validation strictness."""
    PERMISSIVE = 0
    MODERATE = 1
    STRICT = 2


# Dangerous patterns that should be blocked
DANGEROUS_PATTERNS = [
    # Recursive forced deletion
    (r'rm\s+-rf\s+/\s', "Recursive deletion of root directory"),
    (r'rm\s+-rf\s+"?"/\s', "Recursive deletion of root directory"),
    (r'rm\s+-rf\s+\*\s', "Recursive deletion of all files"),
    (r'rm\s+-rf\s+/\*\s', "Recursive deletion of root contents"),
    (r'rm\s+-rf\s+\.\s', "Recursive deletion of current directory"),

    # Force removal of system directories
    (r'rm\s+-rf\s+/usr\s', "Removal of /usr directory"),
    (r'rm\s+-rf\s+/bin\s', "Removal of /bin directory"),
    (r'rm\s+-rf\s+/lib\s', "Removal of /lib directory"),
    (r'rm\s+-rf\s+/etc\s', "Removal of /etc directory"),

    # Data destruction
    (r'shred\s+', "Secure file deletion"),
    (r'dd\s+if=/dev/zero\s+of=', "Overwriting files with zeros"),
    (r'mkfs\s+', "Filesystem format"),
    (r'mkfs\.\w+\s+', "Filesystem format"),

    # Network-based attacks
    (r'curl\s+\|\s*sh\s', "Pipe curl to shell (possible attack)"),
    (r'wget\s+\|\s*sh\s', "Pipe wget to shell (possible attack)"),
    (r'fetch\s+\|\s*sh\s', "Pipe fetch to shell (possible attack)"),
    (r'curl.*\s+-s\s+http://.*\|\s*sh', "Remote curl pipe to shell"),
    (r'wget.*\s+-O-\s+http://.*\|\s*sh', "Remote wget pipe to shell"),

    # System modification via injection
    (r'echo\s+.*>\s*/etc/sudoers', "Modifying sudoers file"),
    (r'echo\s+.*>>\s*/etc/sudoers', "Appending to sudoers file"),
    (r'chmod\s+777\s+/etc', "Making /etc world-writable"),
    (r'chmod\s+777\s+/root', "Making /root world-writable"),
    (r'chmod\s+777\s+/var', "Making /var world-writable"),

    # Environment variable injection
    (r'\$\([^)]+\)', "Command substitution in variable"),
    (r'\$\{[^}]+\}', "Variable expansion in dangerous context"),
    (r'`[^`]+`', "Backtick command substitution"),

    # Fork bombs and DoS
    (r':\(\)\{\s*:\|\:\&\s*\}', "Fork bomb"),
    (r'fork\(\)', "Fork in loop"),
    (r'while\s+true\s*;\s*do', "Infinite loop without break"),

    # Privilege escalation
    (r'sudo\s+su\s', "Escalating to root via sudo su"),
    (r'sudo\s+-i\s', "Escalating to root via sudo -i"),
    (r'su\s+-', "Direct su to root"),
    (r'pkexec\s+', "Using pkexec for privilege escalation"),

    # SSH key manipulation
    (r'ssh.*\s-i\s+.*id_rsa.*@', "SSH with key authentication"),
    (r'eval\s+\$\(ssh-agent', "Starting SSH agent"),
    (r'known_hosts', "Manipulating known_hosts"),

    # Package manager destruction
    (r'yum\s+remove\s+--assumeyes\s+--remove-unused\s+["\']?\*', "Yum removal of all packages"),
    (r'apt-get\s+remove\s+--purge\s+["\']?\*', "Apt removal of all packages"),
    (r'dnf\s+remove\s+["\']?\*', "Dnf removal of all packages"),
    (r'pacman\s+-Rs\s+["\']?\*', "Pacman removal of all packages"),

    # Disk operations
    (r'dd\s+.*of=/dev/sd[a-z]', "Writing directly to disk device"),
    (r'cat\s+/dev/sd[a-z]', "Reading disk device"),
    (r'fdisk\s+/dev/sd[a-z]', "Partitioning disk"),
    (r'parted\s+/dev/sd[a-z]', "Partitioning disk with parted"),

    # Kernel operations
    (r'modprobe\s+-r\s', "Unloading kernel module"),
    (r'insmod\s+', "Loading kernel module"),
    (r'rmmod\s+', "Removing kernel module"),

    # Service manipulation
    (r'systemctl\s+stop\s+', "Stopping system service"),
    (r'systemctl\s+disable\s+', "Disabling system service"),
    (r'service\s+[a-z]+\s+stop', "Stopping service via service command"),

    # Cron manipulation
    (r'crontab\s+-r', "Removing user crontab"),
    (r'crontab\s+-[rd]', "Deleting crontab entries"),
    (r'echo\s+.*>\s*/var/spool/cron/', "Writing to cron directory"),

    # Log destruction
    (r'truncate\s+-s\s+0\s+/var/log/', "Truncating system logs"),
    (r'rm\s+-rf\s+/var/log/', "Removing system logs"),
    (r'>/var/log/', "Redirecting to overwrite logs"),

    # Firewall manipulation
    (r'iptables\s+-F', "Flushing iptables rules"),
    (r'iptables\s+-X', "Deleting iptables chains"),
    (r'ufw\s+disable', "Disabling UFW firewall"),

    # SELinux/AppArmor
    (r'setenforce\s+0', "Disabling SELinux"),
    (r'aa-complain\s+', "Setting AppArmor to complain mode"),
    (r'aa-disable\s+', "Disabling AppArmor profile"),

    # Container escape
    (r'docker\s+run\s+--privileged', "Running privileged container"),
    (r'docker\s+exec\s+.*--privileged', "Executing in privileged container"),
    (r'--network=host', "Disabling container network isolation"),
    (r'--pid=host', "Disabling container PID isolation"),
    (r'--ipc=host', "Disabling container IPC isolation"),

    # Git destructive
    (r'git\s+filter-branch\s+--force', "Git filter-branch operation"),
    (r'git\s+filter-repo\s+', "Git filter-repo operation"),
    (r'git\s+update-ref\s+--delete', "Deleting git reference"),
]

# Commands that require confirmation
CONFIRMATION_REQUIRED_PATTERNS = [
    # File modification
    (r'rm\s+', "Removing files"),
    (r'mv\s+.*\s+.*', "Moving files"),
    (r'cp\s+.*\s+.*', "Copying files"),

    # System commands
    (r'sudo\s+', "Executing with sudo"),
    (r'docker\s+', "Docker operations"),
    (r'kill\s+', "Killing processes"),
    (r'killall\s+', "Killing all processes"),

    # Network operations
    (r'ssh\s+', "SSH connections"),
    (r'scp\s+', "Secure copy"),
    (r'rsync\s+.*\s+--delete', "Rsync with delete"),

    # Installation
    (r'pip\s+install\s+', "Installing Python packages"),
    (r'npm\s+install\s+-g', "Installing global npm packages"),
    (r'yarn\s+global\s+add', "Installing global yarn packages"),

    # Git operations
    (r'git\s+push\s+', "Pushing to remote"),
    (r'git\s+push\s+--force', "Force pushing to remote"),
    (r'git\s+reset\s+--hard', "Hard reset of git"),
    (r'git\s+rebase\s+', "Rebasing git branch"),
]

# Safe environment variables
SAFE_ENV_VARS = [
    "PATH", "HOME", "USER", "SHELL", "TERM",
    "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL",
    "SEARXNG_BASE_URL", "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY",
    "http_proxy", "https_proxy", "no_proxy",
]

# Patterns for detecting variable expansion in dangerous contexts
DANGEROUS_VARIABLE_PATTERNS = [
    r'eval\s+\$\w+',
    r'exec\s+\$\w+',
    r'sh\s+-c\s+["\']?\$\w+',
    r'\|.*\$\w+',
    r'>\s*\$\w+',
    r'<\s*\$\w+',
]


def _check_dangerous_patterns(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check if command matches dangerous patterns.

    Returns (reason, result) tuple.
    """
    for pattern, description in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return description, SecurityResult.DENY

    return None, SecurityResult.ALLOW


def _check_confirmation_required(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check if command requires user confirmation.

    Returns (reason, result) tuple.
    """
    for pattern, description in CONFIRMATION_REQUIRED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return description, SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_environment_injection(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for environment variable injection attempts.

    Returns (reason, result) tuple.
    """
    # Check for unquoted environment variables
    dangerous_vars = [
        r'\$[a-zA-Z_][a-zA-Z0-9_]*',  # $VAR
        r'\$\{[a-zA-Z_][a-zA-Z0-9_]*\}',  # ${VAR}
    ]

    for pattern in dangerous_vars:
        matches = re.findall(pattern, command)
        for match in matches:
            var_name = match[1:].strip("{ }")
            if var_name not in SAFE_ENV_VARS:
                return f"Unknown environment variable: {match}", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_command_injection(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for command injection attempts.

    Returns (reason, result) tuple.
    """
    # Check for multiple commands (chained)
    chain_patterns = [
        r';\s*rm\s+',  # ; rm
        r'&&\s*rm\s+',  # && rm
        r'\|\|\s*rm\s+',  # || rm
        r';\s*wget\s+',  # ; wget
        r'&&\s*wget\s+',  # && wget
        r'\|\|\s*wget\s+',  # || wget
    ]

    for pattern in chain_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return "Command injection detected", SecurityResult.DENY

    # Check for here-docs with variables
    if '<<' in command:
        # Extract content between << and EOF
        heredoc_match = re.search(r'<<\s*(\w+)', command)
        if heredoc_match:
            # This could be dangerous if variables are expanded
            pass

    return None, SecurityResult.ALLOW


def _check_path_traversal(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for path traversal attempts.

    Returns (reason, result) tuple.
    """
    dangerous_paths = [
        r'\.\./',  # ../ path traversal
        r'/\.\./',  # /../ path traversal
        r'\.\.\/\.\.',  # ../../ double traversal
    ]

    for pattern in dangerous_paths:
        if re.search(pattern, command):
            return "Path traversal detected", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_sudo_nopasswd(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for sudo without password.

    Returns (reason, result) tuple.
    """
    if re.search(r'sudo\s+.*-n\s+', command):
        # sudo -n (non-interactive) could be dangerous
        return "Non-interactive sudo command", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_network_operations(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for potentially dangerous network operations.

    Returns (reason, result) tuple.
    """
    network_commands = [
        r'nc\s+',  # netcat
        r'ncat\s+',  # ncat
        r'netcat\s+',  # netcat
        r'curl\s+',  # curl request
        r'wget\s+',  # wget request
        r'fetch\s+',  # fetch request
        r'scp\s+.*:',  # scp to remote
    ]

    for pattern in network_commands:
        if re.search(pattern, command, re.IGNORECASE):
            return "Network operation detected", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_file_permissions(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for permission modification commands.

    Returns (reason, result) tuple.
    """
    permission_commands = [
        r'chmod\s+777',  # World writable
        r'chmod\s+000',  # Remove all permissions
        r'chown\s+',  # Change ownership
        r'chgrp\s+',  # Change group
        r'setfacl\s+',  # Set ACL
    ]

    for pattern in permission_commands:
        if re.search(pattern, command, re.IGNORECASE):
            return "Permission modification detected", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_process_manipulation(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for process manipulation commands.

    Returns (reason, result) tuple.
    """
    process_commands = [
        r'pkill\s+',  # Kill by pattern
        r'killall\s+',  # Kill all
        r'reboot\s+',  # Reboot
        r'shutdown\s+',  # Shutdown
        r'poweroff\s+',  # Power off
        r'init\s+0\s',  # Init 0 (halt)
        r'init\s+6\s',  # Init 6 (reboot)
    ]

    for pattern in process_commands:
        if re.search(pattern, command, re.IGNORECASE):
            return "Process/system manipulation detected", SecurityResult.DENY

    return None, SecurityResult.ALLOW


def _check_shell_features(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for dangerous shell features.

    Returns (reason, result) tuple.
    """
    dangerous_features = [
        (r'eval\s+', "eval command"),
        (r'exec\s+', "exec command"),
        (r'source\s+\$', "sourcing variable"),
        (r'\.\s+\$', "sourcing from variable"),
    ]

    for pattern, description in dangerous_features:
        if re.search(pattern, command):
            return f"Dangerous shell feature: {description}", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_background_process(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for background process execution.

    Returns (reason, result) tuple.
    """
    if re.search(r'&\s*$', command.strip()):
        return "Background process execution", SecurityResult.ASK

    if re.search(r'nohup\s+', command):
        return "No-hup process execution", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_redirection_output(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for dangerous output redirection.

    Returns (reason, result) tuple.
    """
    dangerous_redirects = [
        (r'>\s*/dev/full', "Output to /dev/full"),
        (r'>\s*/dev/null\s+2>&1\s*;\s*exit', "Hide output and exit"),
        (r'>\s*/etc/passwd', "Write to /etc/passwd"),
        (r'>\s*/etc/shadow', "Write to /etc/shadow"),
    ]

    for pattern, description in dangerous_redirects:
        if re.search(pattern, command):
            return description, SecurityResult.DENY

    return None, SecurityResult.ALLOW


def _check_subshell_nesting(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for excessive subshell nesting.

    Returns (reason, result) tuple.
    """
    # Count parentheses depth
    depth = 0
    max_depth = 3

    for char in command:
        if char == '(':
            depth += 1
            if depth > max_depth:
                return "Excessive subshell nesting", SecurityResult.ASK
        elif char == ')':
            depth -= 1

    return None, SecurityResult.ALLOW


def _check_variable_assignment(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for dangerous variable assignments.

    Returns (reason, result) tuple.
    """
    dangerous_assignments = [
        (r'PATH\s*=', "Overwriting PATH variable"),
        (r'HOME\s*=', "Overwriting HOME variable"),
        (r'LD_PRELOAD\s*=', "Setting LD_PRELOAD"),
        (r'LD_LIBRARY_PATH\s*=', "Setting LD_LIBRARY_PATH"),
        (r'DYLD_INSERT_LIBRARIES\s*=', "Setting DYLD_INSERT_LIBRARIES"),
    ]

    for pattern, description in dangerous_assignments:
        if re.search(pattern, command):
            return description, SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_time_operations(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for time-based operations.

    Returns (reason, result) tuple.
    """
    time_commands = [
        r'time\s+',  # time command
        r'at\s+',  # at scheduler
        r'sleep\s+\d+[hm]\s*;\s*rm',  # sleep then remove
    ]

    for pattern in time_commands:
        if re.search(pattern, command, re.IGNORECASE):
            return "Time-based operation detected", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_archive_extraction(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for archive extraction to dangerous locations.

    Returns (reason, result) tuple.
    """
    archive_patterns = [
        r'tar\s+.*\s+-C\s+/\s',  # Extract to root
        r'tar\s+.*\s+-C\s+/etc\s',  # Extract to /etc
        r'unzip\s+.*\s+-d\s+/\s',  # Unzip to root
        r'unzip\s+.*\s+-d\s+/etc\s',  # Unzip to /etc
    ]

    for pattern in archive_patterns:
        if re.search(pattern, command):
            return "Archive extraction to system directory", SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_git_operations(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for potentially dangerous git operations.

    Returns (reason, result) tuple.
    """
    dangerous_git = [
        (r'git\s+stash\s+--drop', "Dropping git stash"),
        (r'git\s+clean\s+-fd', "Force removing untracked files"),
        (r'git\s+reset\s+--hard\s+\$\w+', "Reset to variable SHA"),
        (r'git\s+push\s+--force\s+--no-verify', "Force push without verification"),
    ]

    for pattern, description in dangerous_git:
        if re.search(pattern, command):
            return description, SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_container_operations(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for container-related operations.

    Returns (reason, result) tuple.
    """
    container_patterns = [
        (r'docker\s+run\s+--rm\s+', "Running container and removing after exit"),
        (r'docker\s+exec\s+.*\s+rm\s+', "Removing files in container"),
        (r'podman\s+run\s+--privileged\s+', "Running privileged podman container"),
        (r'kubectl\s+delete\s+', "Deleting Kubernetes resources"),
        (r'kubectl\s+exec\s+.*\s+--\s+rm\s+', "Removing files via kubectl"),
    ]

    for pattern, description in container_patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return description, SecurityResult.ASK

    return None, SecurityResult.ALLOW


def _check_special_characters(command: str) -> Tuple[Optional[str], SecurityResult]:
    """Check for special characters that could be part of injection.

    Returns (reason, result) tuple.
    """
    # Check for null bytes
    if '\x00' in command:
        return "Null byte in command", SecurityResult.DENY

    # Check for newline injection
    if '\n' in command and 'git' in command.lower():
        # Newlines in git commands could be dangerous
        return "Newline injection detected", SecurityResult.DENY

    # Check for special IFS characters
    if any(c in command for c in ['\x1e', '\x1f']):
        return "Special IFS characters detected", SecurityResult.ASK

    return None, SecurityResult.ALLOW


# All validators - order matters for security priority
VALIDATORS = [
    _check_dangerous_patterns,
    _check_command_injection,
    _check_redirection_output,
    _check_process_manipulation,
    _check_environment_injection,
    _check_path_traversal,
    _check_sudo_nopasswd,
    _check_network_operations,
    _check_file_permissions,
    _check_shell_features,
    _check_background_process,
    _check_variable_assignment,
    _check_time_operations,
    _check_archive_extraction,
    _check_git_operations,
    _check_container_operations,
    _check_subshell_nesting,
    _check_confirmation_required,
    _check_special_characters,
]


def validate_bash_command(
    command: str,
    security_level: SecurityLevel = SecurityLevel.MODERATE,
) -> SecurityResult:
    """Validate a bash command for security issues.

    Args:
        command: The command string to validate
        security_level: The security strictness level

    Returns:
        SecurityResult indicating the command's safety
    """
    if not command or not command.strip():
        return SecurityResult.DENY

    command = command.strip()

    # Early exit for empty commands
    if not command:
        return SecurityResult.DENY

    # Track the most severe result
    worst_result = SecurityResult.ALLOW
    reason = None

    for validator in VALIDATORS:
        result_reason, result = validator(command)
        if result == SecurityResult.DENY:
            return SecurityResult.DENY
        elif result == SecurityResult.ASK and worst_result != SecurityResult.DENY:
            worst_result = SecurityResult.ASK
            reason = result_reason
        elif result == SecurityResult.PASSTHROUGH and worst_result == SecurityResult.ALLOW:
            worst_result = SecurityResult.PASSTHROUGH
            reason = result_reason

    # In PERMISSIVE mode, downgrade ASK to ALLOW
    if worst_result == SecurityResult.ASK and security_level == SecurityLevel.PERMISSIVE:
        return SecurityResult.ALLOW

    # In STRICT mode, downgrade ASK to DENY for certain patterns
    if worst_result == SecurityResult.ASK and security_level == SecurityLevel.STRICT:
        if reason and ("rm" in reason.lower() or "sudo" in reason.lower()):
            return SecurityResult.DENY

    return worst_result


def get_security_issue_reason(result: SecurityResult, command: str) -> Optional[str]:
    """Get the reason for the security result.

    Returns a human-readable explanation of why the command
    was flagged or blocked.
    """
    for validator in VALIDATORS:
        reason, result_type = validator(command)
        if result_type == result and reason:
            return reason
    return None


def sanitize_command(command: str) -> str:
    """Attempt to sanitize a command by escaping dangerous elements.

    Note: This is not a guarantee of safety. Use validate_bash_command first.
    """
    # Remove null bytes
    command = command.replace('\x00', '')

    # Escape backticks
    command = command.replace('`', '\\`')

    # Escape $ in variable references (except safe ones)
    # This is a simple approach - not comprehensive
    return command


def validate_batch_commands(commands: List[str]) -> List[Tuple[str, SecurityResult]]:
    """Validate multiple commands in sequence.

    Returns list of (command, result) tuples.
    """
    results = []
    for cmd in commands:
        results.append((cmd, validate_bash_command(cmd)))
    return results


class SecurityValidator:
    """Security validator with state tracking."""

    def __init__(self, security_level: SecurityLevel = SecurityLevel.MODERATE):
        self.security_level = security_level
        self.denied_commands: List[str] = []
        self.asked_commands: List[str] = []
        self.allowed_commands: List[str] = []

    def validate(self, command: str) -> SecurityResult:
        """Validate a command and track the result."""
        result = validate_bash_command(command, self.security_level)

        if result == SecurityResult.DENY:
            self.denied_commands.append(command)
        elif result == SecurityResult.ASK:
            self.asked_commands.append(command)
        else:
            self.allowed_commands.append(command)

        return result

    def get_stats(self) -> Dict[str, int]:
        """Get validation statistics."""
        return {
            "allowed": len(self.allowed_commands),
            "asked": len(self.asked_commands),
            "denied": len(self.denied_commands),
            "total": len(self.allowed_commands) + len(self.asked_commands) + len(self.denied_commands),
        }


from typing import Dict
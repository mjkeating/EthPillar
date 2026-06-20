"""Shared helpers for generating systemd unit file content."""

from typing import List, Optional


def generate_systemd_template(
    description: str,
    user: str,
    exec_start: str,
    extra_env: Optional[List[str]] = None,
    working_dir: Optional[str] = None,
    timeout_stop_sec: int = 900,
    limit_nofile: Optional[int] = None,
    unit_after: Optional[List[str]] = None,
    unit_requires: Optional[List[str]] = None,
) -> str:
    """Generate a systemd service file content.

    Args:
        description: Service description.
        user: System user to run the service.
        exec_start: Multi-line ExecStart value.
        extra_env: Optional list of environment variables.
        working_dir: Optional working directory.
        timeout_stop_sec: Timeout for stopping the service.
        limit_nofile: Optional file descriptor limit.
        unit_after: Additional systemd units that must start before this service.
        unit_requires: Hard dependencies on other systemd units.

    Returns:
        Complete systemd service file content as a string.
    """
    env_str = "".join(f"Environment={e}\n" for e in extra_env) if extra_env else ""
    wd_str = f"WorkingDirectory={working_dir}\n" if working_dir else ""
    nofile_str = f"LimitNOFILE={limit_nofile}\n" if limit_nofile else ""
    after_units = ["network-online.target"]
    wants_units = ["network-online.target"]
    if unit_after:
        after_units.extend(unit_after)
        wants_units.extend(unit_after)
    requires_str = ""
    if unit_requires:
        requires_str = "Requires=" + " ".join(unit_requires) + "\n"
    return f'''[Unit]
Description={description}
After={' '.join(after_units)}
{requires_str}Wants={' '.join(wants_units)}
Documentation=https://docs.coincashew.com

[Service]
Type=simple
User={user}
Group={user}
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec={timeout_stop_sec}
{nofile_str}{wd_str}{env_str}ExecStart={exec_start}

[Install]
WantedBy=multi-user.target
'''


def form_exec_start(args: List[str]) -> str:
    """Join command line arguments into a multi-line ExecStart string."""
    return " \\\n    ".join([a for a in args if a])

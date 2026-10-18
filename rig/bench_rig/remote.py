"""Shell access to the QNX rig over SSH."""

import subprocess
from pathlib import Path

from .config import RigConfig


class RemoteError(RuntimeError):
    pass


class Remote:
    def __init__(self, config: RigConfig):
        self.config = config
        self.target = f"{config.user}@{config.host}"

    def run(self, command: str, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
        """Run a shell command on the rig."""
        proc = subprocess.run(
            ["ssh", *self.config.ssh_options, self.target, command],
            capture_output=True, text=True, timeout=timeout,
        )
        if check and proc.returncode != 0:
            raise RemoteError(f"rig command failed ({proc.returncode}): {command}\n{proc.stderr.strip()}")
        return proc

    def put(self, local: Path, remote_path: str, timeout: float = 120) -> None:
        self._scp([str(local), f"{self.target}:{remote_path}"], timeout)

    def get(self, remote_path: str, local: Path, timeout: float = 120) -> None:
        self._scp([f"{self.target}:{remote_path}", str(local)], timeout)

    def _scp(self, args: list[str], timeout: float) -> None:
        proc = subprocess.run(
            ["scp", "-q", *self.config.ssh_options, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            raise RemoteError(f"scp failed: {' '.join(args)}\n{proc.stderr.strip()}")

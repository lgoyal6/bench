"""Rig configuration, read from the environment. Missing settings fail loudly."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class RigConfig:
    rig_id: str
    host: str
    user: str
    trace_store: Path
    tasks_dir: Path = REPO_ROOT / "tasks"
    workdir: str = "/tmp/bench"
    contestant_core: int = 3
    harness_core: int = 2
    contestant_priority: int = 10
    harness_priority: int = 60
    # QNX `on`: -C pins to a core, -p sets priority and policy (r = round robin, f = FIFO).
    pin_template: str = "on -C {core} -p {prio}{policy}"
    # Verify the flags against `use tracelogger` on the rig image.
    tracelogger_template: str = "tracelogger -s {seconds} -f {path}"
    trace_base_url: str | None = None
    ssh_options: tuple[str, ...] = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "RigConfig":
        env = dict(os.environ if env is None else env)
        required = ["BENCH_RIG_ID", "BENCH_RIG_HOST", "BENCH_RIG_USER", "BENCH_TRACE_STORE"]
        missing = [k for k in required if not env.get(k)]
        if missing:
            raise ConfigError(f"rig not configured, set: {', '.join(missing)}")

        kwargs: dict = {
            "rig_id": env["BENCH_RIG_ID"],
            "host": env["BENCH_RIG_HOST"],
            "user": env["BENCH_RIG_USER"],
            "trace_store": Path(env["BENCH_TRACE_STORE"]),
        }
        optional = {
            "BENCH_TASKS_DIR": ("tasks_dir", Path),
            "BENCH_RIG_WORKDIR": ("workdir", str),
            "BENCH_RIG_CONTESTANT_CORE": ("contestant_core", int),
            "BENCH_RIG_HARNESS_CORE": ("harness_core", int),
            "BENCH_RIG_CONTESTANT_PRIORITY": ("contestant_priority", int),
            "BENCH_RIG_HARNESS_PRIORITY": ("harness_priority", int),
            "BENCH_RIG_PIN_TEMPLATE": ("pin_template", str),
            "BENCH_RIG_TRACELOGGER_TEMPLATE": ("tracelogger_template", str),
            "BENCH_TRACE_BASE_URL": ("trace_base_url", str),
        }
        for var, (field, cast) in optional.items():
            if env.get(var):
                kwargs[field] = cast(env[var])

        config = cls(**kwargs)
        if config.contestant_core == config.harness_core:
            raise ConfigError("contestant and harness must run on different cores")
        return config

"""bench verify | run | replay | doctor

  bench verify L2-servo-track        reference must pass, broken must fail, on the real rig
  bench verify --all
  bench run --task T --repo URL --sha SHA --agent A --model M [--seed N] [--faults F.json]
  bench replay RUN_DIR               re-score a saved run from its timing log
  bench doctor --live                every lane's check_live(); non-zero exit on any problem
"""

import argparse
import hashlib
import json
import random
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

from bench_contracts import Commit, ContestantId, FaultSchedule, RunRequest, RunResult
from bench_rig import QnxRig, RigConfig, compute_metrics, judge, parse_timing_log
from bench_rig.config import REPO_ROOT, ConfigError
from bench_rig.faults import FaultScheduleError, validate_execution
from bench_rig.tasks import list_tasks, load_task


def _rig() -> QnxRig:
    return QnxRig(RigConfig.from_env())


def _repo_head() -> Commit:
    sha = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    origin = subprocess.run(["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip()
    return Commit(repo=origin or str(REPO_ROOT), sha=sha)


def cmd_verify(args: argparse.Namespace) -> int:
    rig = _rig()
    task_ids = list_tasks(rig.config.tasks_dir) if args.all else [args.task]
    if not task_ids:
        print(f"no tasks in {rig.config.tasks_dir}", file=sys.stderr)
        return 1
    commit = _repo_head()
    ok = True
    for task_id in task_ids:
        task = load_task(rig.config.tasks_dir, task_id)
        seed = args.seed if args.seed is not None else random.randrange(2**31)
        for kind, path, expected in (("reference", task.reference_solution, "pass"),
                                     ("broken", task.broken_solution, "fail")):
            request = RunRequest(task_id=task_id, contestant=ContestantId(agent=kind, model=kind),
                                 commit=commit, seed=seed)
            result = rig.run_source(request, REPO_ROOT / path)
            good = result.verdict == expected
            ok &= good
            mark = "ok  " if good else "FAIL"
            print(f"{mark} {task_id} {kind:9} {result.verdict:5} (expected {expected}) "
                  f"seed={seed} run={result.run_id}: {result.verdict_reason}")
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    faults = FaultSchedule.model_validate_json(Path(args.faults).read_text()) if args.faults else None
    request = RunRequest(
        task_id=args.task, contestant=ContestantId(agent=args.agent, model=args.model),
        commit=Commit(repo=args.repo, sha=args.sha),
        seed=args.seed if args.seed is not None else random.randrange(2**31),
        fault_schedule=faults,
    )
    result = _rig().run(request)
    print(result.model_dump_json(indent=2))
    return 0 if result.verdict != "error" else 1


def cmd_replay(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    saved = RunResult.model_validate_json((run_dir / "run_result.json").read_text())
    tasks_dir = Path(args.tasks_dir) if args.tasks_dir else REPO_ROOT / "tasks"
    task = load_task(tasks_dir, saved.request.task_id)
    timing = (run_dir / "timing.csv").read_bytes()
    expected_sha = saved.provenance.get("timing_sha256")
    sha_ok = expected_sha is None or hashlib.sha256(timing).hexdigest() == expected_sha
    log = parse_timing_log(timing.decode())
    metrics = compute_metrics(log, task)
    execution_ok = True
    if saved.request.fault_schedule is not None:
        try:
            execution = (run_dir / "injector.log").read_bytes()
            execution_ok = hashlib.sha256(execution).hexdigest() == saved.provenance.get("injector_sha256")
            validate_execution(execution.decode(), saved.request.fault_schedule, task, log.completed_ns)
        except (OSError, ValueError, FaultScheduleError):
            execution_ok = False
    verdict, reason = judge(log, metrics, task, saved.request.fault_schedule)
    same = sha_ok and execution_ok and verdict == saved.verdict and metrics == saved.metrics
    print(json.dumps({
        "run_id": saved.run_id, "saved_verdict": saved.verdict, "replayed_verdict": verdict,
        "replayed_reason": reason, "metrics_match": metrics == saved.metrics,
        "timing_log_matches_provenance": sha_ok,
        "fault_execution_matches_provenance": execution_ok,
    }, indent=2))
    return 0 if same else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    if not args.live:
        print("use: bench doctor --live", file=sys.stderr)
        return 2
    checks = entry_points(group="bench.healthchecks")
    if not checks:
        print("no health checks installed", file=sys.stderr)
        return 1
    problems = []
    for ep in sorted(checks, key=lambda e: e.name):
        try:
            found = ep.load()().check_live()
        except Exception as e:  # a crashing check is itself a problem to report
            found = [f"health check crashed: {e!r}"]
        status = "ok" if not found else f"{len(found)} problem(s)"
        print(f"{ep.name}: {status}")
        for p in found:
            print(f"  - {p}")
        problems += found
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("verify", help="reference must pass and broken must fail on the rig")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("task", nargs="?")
    g.add_argument("--all", action="store_true")
    p.add_argument("--seed", type=int)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("run", help="run one commit on the rig")
    for flag in ("--task", "--repo", "--sha", "--agent", "--model"):
        p.add_argument(flag, required=True)
    p.add_argument("--seed", type=int)
    p.add_argument("--faults", help="FaultSchedule JSON file")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("replay", help="re-score a saved run from its timing log")
    p.add_argument("run_dir")
    p.add_argument("--tasks-dir")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("doctor", help="check every live integration")
    p.add_argument("--live", action="store_true")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"bench: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

import struct

import pytest

from bench_contracts import Metrics
from bench_rig.audit import TraceAuditError, apply_audit, audit_trace

CORE = 3
NAME = "data/bench/solution/solution"


def _ev(t_us, cpu, cls, event, d1=0, d2=0, state=0):
    return struct.pack("<4I", (state << 30) | (cpu << 24) | (cls << 10) | event, t_us, d1, d2)


def _process(t_us, cpu, ppid, pid, name):
    raw = name.encode() + b"\0"
    raw += b"\0" * (-len(raw) % 8)
    words = struct.unpack(f"<{len(raw) // 4}I", raw)
    out = _ev(t_us, cpu, 4, 192, ppid, pid, state=1)
    pairs = [words[i:i + 2] for i in range(0, len(words), 2)]
    for i, (a, b) in enumerate(pairs):
        out += _ev(t_us, cpu, 4, 192, a, b, state=3 if i == len(pairs) - 1 else 2)
    return out


def _run(t_us, cpu, pid, tid=1):
    return _ev(t_us, cpu, 4, 1, pid, tid)


def _ready(t_us, cpu, pid, tid=1):
    return _ev(t_us, cpu, 4, 2, pid, tid)


def _sched_set(t_us, cpu, priority, ok=True):
    return _ev(t_us, cpu, 2, 89, 0, priority) + _ev(t_us + 1, cpu, 2, 89 + 128, 0 if ok else 22, 0)


def _trace(*events):
    # 1 MHz cycle counter, so timestamps are microseconds
    header = b"TRACE_VERSION::5TRACE_LITTLE_ENDIAN::TRUETRACE_ENCODING::16 byte events" \
             b"TRACE_CYCLES_PER_SEC::1000000TRACE_SYSPAGE_LEN::8TRACE_HEADER_END::"
    return header + b"\xee" * 8 + b"".join(events)


def test_honest_contestant_passes():
    data = _trace(
        _process(0, CORE, 1, 100, "/" + NAME),
        _run(10, CORE, 100), _sched_set(20, CORE, 40),
        _run(30, 0, 100), _run(33, 0, 5),   # a 3 us wake slice off-core
        _ready(1000, CORE, 100),
    )
    result = audit_trace(data, NAME, CORE)
    assert result.violations == []
    assert result.contestant_pids == [100]
    assert result.priorities_set == [40]
    assert result.off_core_us == pytest.approx(3)


def test_priority_above_limit_fails():
    data = _trace(_process(0, CORE, 1, 100, NAME), _run(10, CORE, 100), _sched_set(20, CORE, 63))
    result = audit_trace(data, NAME, CORE)
    assert result.violations == ["set priority 63 (limit 50)"]
    assert result.metrics()["audit_max_priority_set"] == 63


def test_failed_sched_set_and_other_processes_do_not_count():
    data = _trace(
        _process(0, CORE, 1, 100, NAME), _process(0, 2, 1, 200, "harness"),
        _run(10, CORE, 100), _sched_set(20, CORE, 63, ok=False),
        _run(30, 2, 200), _sched_set(40, 2, 60),
    )
    result = audit_trace(data, NAME, CORE)
    assert result.violations == []
    assert result.priorities_set == []


def test_running_on_another_core_fails_and_children_count():
    data = _trace(
        _process(0, CORE, 1, 100, NAME), _process(5, CORE, 100, 101, "worker"),
        _run(10, 1, 101), _ready(5010, 1, 101),  # child ran 5 ms on core 1
    )
    result = audit_trace(data, NAME, CORE)
    assert result.contestant_pids == [100, 101]
    assert result.cpus == {1: 1}
    assert result.violations == ["ran 5.00 ms off its assigned core 3 (limit 2 ms)"]


def test_timestamp_wrap_is_handled():
    data = _trace(_process(0, CORE, 1, 100, NAME), _run(0xFFFFFF00, 0, 100), _run(0x100, 0, 5))
    assert audit_trace(data, NAME, CORE).off_core_us == pytest.approx(0x200)


def test_bad_trace_and_missing_contestant_are_errors():
    with pytest.raises(TraceAuditError):
        audit_trace(b"stand-in-trace\n", NAME, CORE)
    with pytest.raises(TraceAuditError, match="no process"):
        audit_trace(_trace(_process(0, CORE, 1, 100, "other")), NAME, CORE)


def test_apply_audit_turns_a_violation_into_a_fail_but_keeps_errors():
    data = _trace(_process(0, CORE, 1, 100, NAME), _run(10, CORE, 100), _sched_set(20, CORE, 63))
    audit = audit_trace(data, NAME, CORE)
    metrics = Metrics(trials=10, deadline_hit_rate=1.0)
    m, verdict, reason = apply_audit(audit, metrics, "pass", "all deadlines met")
    assert verdict == "fail" and reason.startswith("broke rig rules: set priority 63")
    assert m.extra["audit_max_priority_set"] == 63
    assert apply_audit(audit, metrics, "error", "rig fault")[1:] == ("error", "rig fault")

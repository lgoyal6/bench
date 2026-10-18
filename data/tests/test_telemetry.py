import hashlib
import logging

import httpx
import pytest
import sentry_sdk
from bench_data.telemetry import Telemetry, check_live
from sentry_sdk.transport import Transport


def test_sentry_span_and_log_envelopes_stay_local_and_exclude_unrelated_logs(monkeypatch):
    envelopes = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    real_client = sentry_sdk.Client
    monkeypatch.setenv("SENTRY_DSN", "https://unit-test@unit.test/1")
    monkeypatch.setattr(
        sentry_sdk, "Client", lambda **kwargs: real_client(transport=MemoryTransport, **kwargs)
    )
    telemetry = Telemetry()
    try:
        with telemetry.job("job_" + "a" * 32) as trace_id:
            with telemetry.contestant_call():
                logging.getLogger("unit-test-unrelated").warning("DO_NOT_TRANSMIT_SECRET")
            assert len(trace_id) == 32
        with pytest.raises(RuntimeError):
            with telemetry.job("job_" + "b" * 32):
                raise RuntimeError("DO_NOT_TRANSMIT_EXCEPTION")
        telemetry.client.flush()
        serialized = b"\n".join(envelope.serialize() for envelope in envelopes)
        assert b"DO_NOT_TRANSMIT_SECRET" not in serialized
        assert b"DO_NOT_TRANSMIT_EXCEPTION" not in serialized
        assert b"Bench job failed" in serialized
        assert b"Bench job completed" in serialized
        assert b"bench.contestant" in serialized
        assert b"job_" + b"a" * 32 in serialized
    finally:
        telemetry.close()


def test_missing_dsn_never_reports_live(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert check_live()
    with pytest.raises(ValueError, match="not configured"):
        Telemetry()


@pytest.mark.parametrize(
    "condition", ["verified", "wrong_event", "wrong_dsn", "wrong_message", "redirect", "forbidden"]
)
def test_health_requires_exact_cloud_probe_readback(monkeypatch, condition):
    dsn = "https://unit-test@unit.test/1"
    event_id = "a" * 32
    for name, value in {
        "SENTRY_DSN": dsn,
        "SENTRY_AUTH_TOKEN": "private-test-token",
        "SENTRY_ORG": "unit-org",
        "SENTRY_PROJECT": "unit-project",
        "SENTRY_PROBE_EVENT_ID": event_id,
    }.items():
        monkeypatch.setenv(name, value)
    calls = []

    def handle(request):
        calls.append(request)
        assert (
            str(request.url)
            == f"https://sentry.io/api/0/projects/unit-org/unit-project/events/{event_id}/"
        )
        body = {
            "eventID": event_id if condition != "wrong_event" else "b" * 32,
            "message": "Bench telemetry connectivity check"
            if condition != "wrong_message"
            else "Other event",
            "tags": [
                {
                    "key": "bench.dsn_hash",
                    "value": hashlib.sha256(dsn.encode()).hexdigest()
                    if condition != "wrong_dsn"
                    else "other",
                }
            ],
        }
        return httpx.Response(
            302 if condition == "redirect" else 403 if condition == "forbidden" else 200,
            json=body,
            headers={"Location": "https://untrusted.test/"},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    problems = check_live()
    assert bool(problems) == (condition != "verified")
    assert len(calls) == 1
    assert "private-test-token" not in str(problems)


def test_probe_emits_connectivity_event_without_a_fake_run(monkeypatch):
    from bench_data import telemetry as module

    sent = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            sent.append(envelope)

    real_client = sentry_sdk.Client
    monkeypatch.setenv("SENTRY_DSN", "https://unit-test@unit.test/1")
    monkeypatch.setattr(
        sentry_sdk, "Client", lambda **kwargs: real_client(transport=MemoryTransport, **kwargs)
    )
    event_id = module.probe()
    assert len(event_id) == 32
    raw = b"\n".join(envelope.serialize() for envelope in sent)
    assert event_id.encode() in raw
    assert b"Bench telemetry connectivity check" in raw
    assert b"bench.dsn_hash" in raw
    assert b"job_" not in raw and b"run_" not in raw


@pytest.mark.parametrize("command", ["probe", "doctor"])
def test_sentry_cli_distinguishes_emission_from_verified_readback(monkeypatch, capsys, command):
    import json
    import sys

    from bench_data import runtime, telemetry

    monkeypatch.setattr(sys, "argv", ["bench-data", command, "--backend", "sentry"])
    monkeypatch.setattr(telemetry, "probe", lambda: "c" * 32)
    monkeypatch.setattr(telemetry, "check_live", lambda: [])
    if command == "doctor":
        with pytest.raises(SystemExit) as exit_info:
            runtime.main()
        assert exit_info.value.code == 0
        assert json.loads(capsys.readouterr().out) == {"backend": "sentry", "problems": []}
    else:
        runtime.main()
        assert json.loads(capsys.readouterr().out) == {
            "event_id": "c" * 32,
            "readback_verified": False,
        }


def test_dsn_without_readback_credentials_never_claims_live(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://unit-test@unit.test/1")
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    assert check_live()

"""Opt-in Sentry job tracing and structured logs without prompts or credentials."""

import hashlib
import logging
import os
import re
from contextlib import contextmanager

import httpx
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

LOGGER = logging.getLogger("bench.telemetry")
MESSAGES = {"Bench job started", "Bench job completed", "Bench job failed"}
PROBE_MESSAGE = "Bench telemetry connectivity check"


def filter_log(log, hint):
    if (
        log.get("attributes", {}).get("logger.name") != LOGGER.name
        or log.get("body") not in MESSAGES
    ):
        return None
    log["attributes"] = {
        key: value
        for key, value in log.get("attributes", {}).items()
        if key in {"logger.name", "bench.job_id", "sentry.origin"}
    }
    return log


class Telemetry:
    def __init__(self):
        dsn = os.environ.get("SENTRY_DSN")
        if not dsn:
            raise ValueError("SENTRY_DSN is not configured; no Sentry delivery claimed")
        self.client = sentry_sdk.Client(
            dsn=dsn,
            data_collection={
                "user_info": False,
                "cookies": {"mode": "off"},
                "http_headers": {"request": {"mode": "off"}},
                "http_bodies": [],
                "url_query_params": {"mode": "off"},
                "graphql": {"document": False, "variables": False},
                "gen_ai": {"inputs": False, "outputs": False},
                "database_query_data": False,
                "queues": False,
                "stack_frame_variables": False,
                "frame_context_lines": 0,
            },
            default_integrations=False,
            traces_sample_rate=1.0,
            max_breadcrumbs=0,
            integrations=[
                LoggingIntegration(
                    level=None,
                    event_level=None,
                    capture_sentry_logs=True,
                )
            ],
            before_send_log=filter_log,
        )
        LOGGER.setLevel(logging.INFO)

    @contextmanager
    def job(self, job_id: str):
        if not re.fullmatch(r"job_[0-9a-f]{32}", job_id):
            raise ValueError("Telemetry requires a Bench job ID, never a prompt or user identifier")
        with sentry_sdk.isolation_scope() as scope:
            scope.set_client(self.client)
            with sentry_sdk.start_transaction(op="bench.job", name="Bench job") as span:
                span.set_tag("bench.job_id", job_id)
                LOGGER.info("Bench job started", extra={"bench.job_id": job_id})
                try:
                    yield span.trace_id
                except Exception:
                    span.set_status("internal_error")
                    LOGGER.error("Bench job failed", extra={"bench.job_id": job_id})
                    raise
                else:
                    if span.status == "internal_error":
                        LOGGER.error("Bench job failed", extra={"bench.job_id": job_id})
                    else:
                        span.set_status("ok")
                        LOGGER.info("Bench job completed", extra={"bench.job_id": job_id})

    @contextmanager
    def contestant_call(self):
        with sentry_sdk.start_span(op="bench.contestant", name="Contestant call"):
            yield

    @contextmanager
    def rig_call(self):
        with sentry_sdk.start_span(op="bench.rig", name="Rig execution"):
            yield

    def record_result(self, verdict: str):
        span = sentry_sdk.get_current_span()
        if span:
            span.set_tag("bench.verdict", verdict)
            if verdict == "error":
                span.set_status("internal_error")

    def close(self):
        self.client.flush(timeout=5)
        self.client.close()


def probe() -> str:
    """Send an explicit connectivity event, never a synthetic job or run."""
    telemetry = Telemetry()
    try:
        event_id = telemetry.client.capture_event(
            {
                "message": PROBE_MESSAGE,
                "level": "info",
                "tags": {
                    "bench.dsn_hash": hashlib.sha256(os.environ["SENTRY_DSN"].encode()).hexdigest()
                },
            }
        )
        if not event_id:
            raise ValueError("Sentry did not accept the connectivity event")
        return event_id
    finally:
        telemetry.close()


def check_live() -> list[str]:
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return ["Sentry: SENTRY_DSN is not configured"]
    token = os.environ.get("SENTRY_AUTH_TOKEN")
    org = os.environ.get("SENTRY_ORG", "")
    project = os.environ.get("SENTRY_PROJECT", "")
    event_id = os.environ.get("SENTRY_PROBE_EVENT_ID", "")
    if (
        not token
        or not all(re.fullmatch(r"[a-zA-Z0-9_-]+", slug) for slug in (org, project))
        or not re.fullmatch(r"[0-9a-f]{32}", event_id)
    ):
        return ["Sentry: configure read-only API access and SENTRY_PROBE_EVENT_ID for readback"]
    try:
        # Keep the token on Sentry's API origin, including on redirects/errors.
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.get(
                f"https://sentry.io/api/0/projects/{org}/{project}/events/{event_id}/",
                headers={"Authorization": "Bearer " + token},
            )
            response.raise_for_status()
            event = response.json()
        tags = {tag["key"]: tag["value"] for tag in event.get("tags", [])}
        if (
            event.get("eventID") != event_id
            or event.get("message") != PROBE_MESSAGE
            or tags.get("bench.dsn_hash") != hashlib.sha256(dsn.encode()).hexdigest()
        ):
            raise ValueError("Probe readback does not match this DSN")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return ["Sentry: connectivity event readback failed; cloud delivery remains unverified"]
    return []

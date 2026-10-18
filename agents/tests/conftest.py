import json
from pathlib import Path

import pytest
from bench_agents.config import MarketConfig
from bench_agents.market import Representative
from bench_contracts.messages import JobPosted
from bench_contracts.run import RunResult
from uagents_core.identity import Identity


@pytest.fixture
def record():
    # Shared fixtures are format examples and are consumed only by these tests.
    path = Path(__file__).parents[2] / "contracts/tests/fixtures/run_result.json"
    return RunResult.model_validate_json(path.read_text())


@pytest.fixture
def rep(tmp_path, record):
    path = tmp_path / "records.jsonl"
    path.write_text(record.model_dump_json() + "\n")
    return Representative(
        name="devin-opus",
        address=Identity.from_seed("unit-test-representative", 0).address,
        contestant=record.request.contestant,
        price_usd=5,
        records=path,
        seed_env="BENCH_TEST_REP_SEED",
        port=8101,
    )


@pytest.fixture
def config(rep):
    return MarketConfig(
        coordinator_address=Identity.from_seed("unit-test-coordinator", 0).address,
        representatives=[rep],
    )


@pytest.fixture
def job(record):
    return JobPosted(
        job_id="job_" + "a" * 32,
        task_id=record.request.task_id,
        customer="customer",
        budget_usd=10,
        max_bond_usd=8,
    )


@pytest.fixture
def post_text(record):
    return json.dumps(
        {
            "action": "post_job",
            "task_id": record.request.task_id,
            "budget_usd": 10,
            "max_bond_usd": 8,
        }
    )

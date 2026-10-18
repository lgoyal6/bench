import pytest
from bench_data.snowflake import Snowflake


@pytest.fixture
def sql_api(monkeypatch):
    values = {
        "HOST": "test-account.snowflakecomputing.com",
        "PAT": "unit-test-only",
        "DATABASE": "BENCH_TEST",
        "SCHEMA": "PUBLIC",
        "WAREHOUSE": "TEST",
        "ROLE": "TEST",
    }
    for key, value in values.items():
        monkeypatch.setenv("SNOWFLAKE_" + key, value)
    connection = Snowflake()
    yield connection
    connection.close()

from importlib.metadata import distribution

from bench_agents import check_live


def test_contract_health_entrypoint_reports_missing_configuration(monkeypatch):
    monkeypatch.delenv("BENCH_MARKET_CONFIG", raising=False)
    assert check_live() == ["M1 market: BENCH_MARKET_CONFIG is not configured"]


def test_contract_health_entrypoint_reports_invalid_configuration(monkeypatch, tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("not JSON")
    monkeypatch.setenv("BENCH_MARKET_CONFIG", str(path))
    assert check_live() == ["M1 market: BENCH_MARKET_CONFIG is unreadable or invalid"]


def test_installed_doctor_plugins_report_market_and_payment_blockers(monkeypatch):
    monkeypatch.delenv("BENCH_MARKET_CONFIG", raising=False)
    plugins = {
        ep.name: ep.load()()
        for ep in distribution("bench-market").entry_points
        if ep.group == "bench.healthchecks"
    }
    assert set(plugins) == {"market", "payments"}
    assert plugins["market"].check_live() == ["M1 market: BENCH_MARKET_CONFIG is not configured"]
    assert "simulation only" in plugins["payments"].check_live()[0]

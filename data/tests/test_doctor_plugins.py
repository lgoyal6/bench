from importlib.metadata import distribution

from bench_data import snowflake, telemetry, tiger


def test_installed_doctor_plugins_invoke_their_backend_checks(monkeypatch):
    seen = []

    def check(name):
        def run():
            seen.append(name)
            return [f"{name}: unavailable"]

        return run

    for name, backend in (("tiger", tiger), ("snowflake", snowflake), ("sentry", telemetry)):
        monkeypatch.setattr(backend, "check_live", check(name))
    plugins = {
        ep.name: ep.load()()
        for ep in distribution("bench-data").entry_points
        if ep.group == "bench.healthchecks"
    }
    assert set(plugins) == {"tiger", "snowflake", "sentry"}
    for name, plugin in plugins.items():
        assert plugin.check_live() == [f"{name}: unavailable"]
    assert set(seen) == set(plugins)

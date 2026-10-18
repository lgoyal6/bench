"""Zero-argument HealthCheck factories for the shared doctor plugin group."""

from . import snowflake, telemetry, tiger


class TigerHealth:
    def check_live(self) -> list[str]:
        return tiger.check_live()


class SnowflakeHealth:
    def check_live(self) -> list[str]:
        return snowflake.check_live()


class SentryHealth:
    def check_live(self) -> list[str]:
        return telemetry.check_live()

"""Synchronous HealthCheck entry point for Tijil's future bench doctor CLI."""

import asyncio
import os
from pathlib import Path

from .config import MarketConfig
from .config import check_live as check_config_live
from .payments import check_live as check_payment_live


def check_live() -> list[str]:
    path = os.environ.get("BENCH_MARKET_CONFIG")
    if not path:
        return ["M1 market: BENCH_MARKET_CONFIG is not configured"]
    try:
        config = MarketConfig.read(Path(path).resolve())
    except (OSError, ValueError):
        return ["M1 market: BENCH_MARKET_CONFIG is unreadable or invalid"]
    return asyncio.run(check_config_live(config))


class MarketHealth:
    def check_live(self) -> list[str]:
        return check_live()


class PaymentHealth:
    def check_live(self) -> list[str]:
        return check_payment_live()

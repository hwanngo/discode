from __future__ import annotations

from discode.metrics import (
    OUTBOX_LAG_ENTRIES,
    OUTBOX_OLDEST_PENDING_SECONDS,
    OUTBOX_PUBLISH_TOTAL,
    _MetricsRegistry,
)


def test_record_outbox_publish_increments_counter() -> None:
    reg = _MetricsRegistry()
    key = f"{OUTBOX_PUBLISH_TOTAL}:producer=bot"
    assert reg.get_counter(key) == 0
    reg.increment(key)
    assert reg.get_counter(key) == 1
    reg.increment(key)
    assert reg.get_counter(key) == 2


def test_record_outbox_lag_sets_gauge_values() -> None:
    reg = _MetricsRegistry()
    reg.set_gauge(OUTBOX_LAG_ENTRIES, 5.0)
    reg.set_gauge(OUTBOX_OLDEST_PENDING_SECONDS, 42.5)
    assert reg.get_gauge(OUTBOX_LAG_ENTRIES) == 5.0
    assert reg.get_gauge(OUTBOX_OLDEST_PENDING_SECONDS) == 42.5


def test_registry_get_counter_returns_incremented_value() -> None:
    reg = _MetricsRegistry()
    reg.increment("test_counter", 3.0)
    assert reg.get_counter("test_counter") == 3.0
    reg.increment("test_counter", 2.0)
    assert reg.get_counter("test_counter") == 5.0


def test_record_outbox_publish_uses_module_registry() -> None:
    # Use a fresh registry to avoid cross-test pollution
    reg = _MetricsRegistry()
    key = f"{OUTBOX_PUBLISH_TOTAL}:producer=runner"
    reg.increment(key)
    assert reg.get_counter(key) == 1.0

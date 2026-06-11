from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Simple counter/gauge registry (no prometheus_client dependency required at this phase)
# Use lazy import of prometheus_client if available, otherwise stub


class _MetricsRegistry:
    """Simple metrics registry. Uses prometheus_client if available."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}

    def counter(self, name: str, labels: dict[str, str] | None = None) -> _Counter:
        return _Counter(self, name, labels or {})

    def gauge(self, name: str, labels: dict[str, str] | None = None) -> _Gauge:
        return _Gauge(self, name, labels or {})

    def increment(self, name: str, value: float = 1.0) -> None:
        self._counters[name] = self._counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        self._gauges[name] = value

    def get_counter(self, name: str) -> float:
        return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0)


class _Counter:
    def __init__(self, registry: _MetricsRegistry, name: str, labels: dict[str, str]) -> None:
        self._registry = registry
        self._name = name
        self._labels = labels

    def inc(self, amount: float = 1.0) -> None:
        key = f"{self._name}:{','.join(f'{k}={v}' for k, v in self._labels.items())}"
        self._registry.increment(key, amount)


class _Gauge:
    def __init__(self, registry: _MetricsRegistry, name: str, labels: dict[str, str]) -> None:
        self._registry = registry
        self._name = name
        self._labels = labels

    def set(self, value: float) -> None:
        key = f"{self._name}:{','.join(f'{k}={v}' for k, v in self._labels.items())}"
        self._registry.set_gauge(key, value)


# Module-level registry
REGISTRY = _MetricsRegistry()

# Named metrics
OUTBOX_PUBLISH_TOTAL = "dab_outbox_publish_total"
OUTBOX_LAG_ENTRIES = "dab_outbox_lag_entries"
OUTBOX_OLDEST_PENDING_SECONDS = "dab_outbox_oldest_pending_seconds"
DISPATCHER_ROUTE_DEPTH = "dispatcher_route_depth"
RUNNER_HEARTBEAT_LAG = "runner_heartbeat_lag_seconds"


def record_outbox_publish(producer: str) -> None:
    REGISTRY.increment(f"{OUTBOX_PUBLISH_TOTAL}:producer={producer}")


def record_outbox_lag(pending_count: int, oldest_seconds: float) -> None:
    REGISTRY.set_gauge(OUTBOX_LAG_ENTRIES, float(pending_count))
    REGISTRY.set_gauge(OUTBOX_OLDEST_PENDING_SECONDS, oldest_seconds)

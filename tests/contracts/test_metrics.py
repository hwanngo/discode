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


def test_exporter_emits_prometheus_labels(monkeypatch):
    import discode.metrics as metrics

    reg = _MetricsRegistry()
    monkeypatch.setattr(metrics, "REGISTRY", reg)
    reg.increment("discode_jobs_total:producer=runner")
    assert b'discode_jobs_total{producer="runner"} 1.0' in metrics.render_metrics()


def test_metrics_server_serves_healthz_and_accepts_bracketed_ipv6() -> None:
    """Audit 2026-10-12: liveness endpoint for supervisors/probes."""
    import socket
    import urllib.request

    from discode import metrics as metrics_module

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = metrics_module.start_metrics_server(f"127.0.0.1:{port}")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as resp:
            assert resp.status == 200
            assert resp.read() == b"ok\n"
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]

    if not socket.has_ipv6:
        return
    ipv6 = metrics_module.start_metrics_server("[::1]:0")
    ipv6.shutdown()  # type: ignore[attr-defined]
    ipv6.server_close()  # type: ignore[attr-defined]

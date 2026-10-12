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


def record_outbox_publish(producer: str) -> None:
    REGISTRY.increment(f"{OUTBOX_PUBLISH_TOTAL}:producer={producer}")


def record_outbox_lag(pending_count: int, oldest_seconds: float) -> None:
    REGISTRY.set_gauge(OUTBOX_LAG_ENTRIES, float(pending_count))
    REGISTRY.set_gauge(OUTBOX_OLDEST_PENDING_SECONDS, oldest_seconds)


def render_metrics() -> bytes:
    """Expose the registry in Prometheus text format (stable label order)."""
    import json

    lines = []
    for key, value in list(REGISTRY._counters.items()) + list(REGISTRY._gauges.items()):
        name, _, labels = key.partition(":")
        suffix = ""
        if labels:
            pairs = [part.split("=", 1) for part in labels.split(",")]
            suffix = "{" + ",".join(f"{k}={json.dumps(v)}" for k, v in pairs) + "}"
        lines.append(f"{name}{suffix} {value}\n")
    return "".join(lines).encode()


def start_metrics_server(bind: str) -> object:
    """Start a loopback-only HTTP exporter; caller owns shutdown."""
    import ipaddress
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    host, port = bind.rsplit(":", 1)
    host = host.strip("[]")  # accept bracketed IPv6, e.g. [::1]:9101
    if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
        raise ValueError("Metrics bind must be loopback; use an authenticated proxy remotely")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                # Liveness only: the process is up and serving. Readiness depends
                # on Postgres/Redis; watch the error counters for those.
                body, content_type = b"ok\n", "text/plain"
            elif self.path == "/metrics":
                body, content_type = render_metrics(), "text/plain; version=0.0.4"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    server = Server((host, int(port)), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

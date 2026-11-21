# config settings for the investigator service
import os

# URLs for telemetry back‑ends (override via env vars when running)
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")
TEMPO_URL = os.getenv("TEMPO_URL", "http://localhost:3200")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

SERVICE_HANDLERS = {
    "order-service": "/orders",
    "inventory-service": "/(stock|reserve)",
    "payment-service": "/pay",
}

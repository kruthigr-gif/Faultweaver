import logging
import os
import httpx
from fastapi import FastAPI, HTTPException, Depends, Header
from auth import require_user
from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="payment-service")

# OpenTelemetry setup
resource = Resource(attributes={"service.name": "payment-service"})
trace.set_tracer_provider(TracerProvider(resource=resource))
otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
otlp_exporter = OTLPSpanExporter(endpoint=otel_endpoint, insecure=True)
trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(otlp_exporter))
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=otel_endpoint, insecure=True))
)
set_logger_provider(logger_provider)
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(
    LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
)
logger = logging.getLogger(__name__)
FastAPIInstrumentor().instrument_app(app)
Instrumentator().instrument(app).expose(app)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/pay")
async def pay(
    amount: float,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _user=Depends(require_user),
):
    if amount <= 0:
        logger.warning("Rejected invalid payment amount=%s", amount)
        raise HTTPException(status_code=400, detail="Invalid amount")

    provider_mode = os.getenv("PAYMENT_PROVIDER_MODE", "demo").lower()
    if provider_mode == "demo":
        logger.info("Demo payment approved amount=%s", amount)
        return {"status": "approved", "amount": amount, "provider": "demo"}

    provider_url = os.getenv("PAYMENT_PROVIDER_URL")
    provider_token = os.getenv("PAYMENT_PROVIDER_TOKEN")
    if not provider_url or not provider_token:
        raise HTTPException(status_code=503, detail="Payment provider is not configured")

    headers = {
        "Authorization": f"Bearer {provider_token}",
        "Idempotency-Key": idempotency_key or "",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
            response = await client.post(
                provider_url,
                headers=headers,
                json={"amount": amount, "currency": os.getenv("PAYMENT_CURRENCY", "USD")},
            )
    except httpx.RequestError as exc:
        logger.exception("Payment provider request failed")
        raise HTTPException(status_code=502, detail="Payment provider unavailable") from exc

    if response.status_code >= 500:
        raise HTTPException(status_code=502, detail="Payment provider unavailable")
    if response.status_code in {402, 409}:
        raise HTTPException(status_code=402, detail="Payment declined")
    if not response.is_success:
        raise HTTPException(status_code=502, detail="Payment provider rejected the request")

    provider_result = response.json()
    logger.info("Payment approved amount=%s", amount)
    return {"status": "approved", "amount": amount, "provider": "external", "provider_result": provider_result}

import logging
import os
from fastapi import FastAPI, HTTPException
import httpx
import uuid
from fastapi import Depends, Header
from auth import bearer_scheme, require_user
from contextlib import asynccontextmanager
import asyncpg
from fastapi.security import HTTPAuthorizationCredentials
from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from prometheus_fastapi_instrumentator import Instrumentator

@asynccontextmanager
async def lifespan(app: FastAPI):
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        dsn = database_url.replace("postgres://", "postgresql://", 1)
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
        app.state.db = pool
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    idempotency_key TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
    yield
    pool = getattr(app.state, "db", None)
    if pool:
        await pool.close()


app = FastAPI(title="order-service", lifespan=lifespan)

# OpenTelemetry setup
resource = Resource(attributes={"service.name": "order-service"})
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
HTTPXClientInstrumentor().instrument()
Instrumentator().instrument(app).expose(app)

INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory-service")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://payment-service")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/orders")
async def create_order(
    item_id: str,
    quantity: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    _user=Depends(require_user),
):
    if not item_id or len(item_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid item_id")
    if quantity <= 0 or quantity > 1000:
        raise HTTPException(status_code=400, detail="Quantity must be between 1 and 1000")

    key = idempotency_key or str(uuid.uuid4())
    authorization = (
        f"{credentials.scheme} {credentials.credentials}" if credentials else ""
    )
    pool = getattr(app.state, "db", None)
    if pool:
        async with pool.acquire() as connection:
            existing = await connection.fetchrow(
                "SELECT item_id, quantity, status FROM orders WHERE idempotency_key=$1",
                key,
            )
        if existing:
            return {
                "status": existing["status"],
                "item_id": existing["item_id"],
                "quantity": existing["quantity"],
                "idempotency_key": key,
            }

    timeout = httpx.Timeout(5.0, connect=2.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            inv_resp = await client.post(
                f"{INVENTORY_URL}/reserve",
                params={"item_id": item_id, "quantity": quantity},
                headers={"Authorization": authorization or ""},
            )
            inv_resp.raise_for_status()
        except httpx.RequestError as exc:
            logger.exception("Inventory reservation failed")
            raise HTTPException(status_code=502, detail="Inventory service unavailable") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                raise HTTPException(status_code=409, detail="Insufficient stock") from exc
            raise HTTPException(status_code=502, detail="Inventory reservation failed") from exc

        try:
            pay_resp = await client.post(
                f"{PAYMENT_URL}/pay",
                params={"amount": quantity * 10},
                headers={
                    "Idempotency-Key": key,
                    "Authorization": authorization or "",
                },
            )
            pay_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                await client.post(
                    f"{INVENTORY_URL}/release",
                    params={"item_id": item_id, "quantity": quantity},
                    headers={"Authorization": authorization or ""},
                )
            except httpx.RequestError:
                logger.exception("Inventory rollback failed")
            if exc.response.status_code == 402:
                raise HTTPException(status_code=402, detail="Payment declined") from exc
            raise HTTPException(status_code=502, detail="Payment failed") from exc
        except httpx.RequestError as exc:
            try:
                await client.post(
                    f"{INVENTORY_URL}/release",
                    params={"item_id": item_id, "quantity": quantity},
                    headers={"Authorization": authorization or ""},
                )
            except httpx.RequestError:
                logger.exception("Inventory rollback failed")
            raise HTTPException(status_code=502, detail="Payment service unavailable") from exc

    if pool:
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO orders(idempotency_key, item_id, quantity, status)
                VALUES($1, $2, $3, 'order_created')
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                key,
                item_id,
                quantity,
            )

    logger.info("Order created for item_id=%s quantity=%s", item_id, quantity)
    return {
        "status": "order_created",
        "item_id": item_id,
        "quantity": quantity,
        "idempotency_key": key,
    }

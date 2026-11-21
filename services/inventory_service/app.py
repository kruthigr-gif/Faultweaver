import logging
import os
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi import Depends
from contextlib import asynccontextmanager
import asyncpg
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
                CREATE TABLE IF NOT EXISTS inventory (
                    item_id TEXT PRIMARY KEY,
                    stock INTEGER NOT NULL CHECK (stock >= 0)
                )
                """
            )
            await connection.executemany(
                "INSERT INTO inventory(item_id, stock) VALUES($1, $2) "
                "ON CONFLICT(item_id) DO NOTHING",
                [("itemA", 10000), ("itemB", 10000)],
            )
    yield
    pool = getattr(app.state, "db", None)
    if pool:
        await pool.close()


app = FastAPI(title="inventory-service", lifespan=lifespan)

# OpenTelemetry setup
resource = Resource(attributes={"service.name": "inventory-service"})
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
# Prometheus instrumentation (exposes /metrics automatically)
Instrumentator().instrument(app).expose(app)

# Simple in‑memory stock store
STOCK_DB = {"itemA": 10000, "itemB": 10000}

# Fault injection flags (set via environment variables)
LATENCY_MS = int(os.getenv("LATENCY_MS", "0"))
FORCE_ERROR = os.getenv("FORCE_ERROR")


def raise_injected_error(item_id: str):
    if FORCE_ERROR:
        logger.error("Injected inventory error for item_id=%s", item_id)
        raise HTTPException(
            status_code=int(FORCE_ERROR),
            detail="Injected fault",
        )


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/stock")
async def get_stock(item_id: str, _user=Depends(require_user)):
    # Inject artificial latency if configured
    if LATENCY_MS > 0:
        await asyncio.sleep(LATENCY_MS / 1000.0)
    # Inject forced HTTP error response if configured
    raise_injected_error(item_id)
    pool = getattr(app.state, "db", None)
    if pool:
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT stock FROM inventory WHERE item_id=$1", item_id
            )
        if row is None:
            logger.warning("Stock lookup failed for unknown item_id=%s", item_id)
            raise HTTPException(status_code=404, detail="Item not found")
        stock = row["stock"]
    else:
        stock = STOCK_DB.get(item_id)
        if stock is None:
            logger.warning("Stock lookup failed for unknown item_id=%s", item_id)
            raise HTTPException(status_code=404, detail="Item not found")
    logger.info("Stock lookup succeeded for item_id=%s", item_id)
    return {"item_id": item_id, "stock": stock}


@app.post("/reserve")
async def reserve_stock(item_id: str, quantity: int, _user=Depends(require_user)):
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")
    raise_injected_error(item_id)

    pool = getattr(app.state, "db", None)
    if pool:
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE inventory
                SET stock = stock - $2
                WHERE item_id=$1 AND stock >= $2
                RETURNING stock
                """,
                item_id,
                quantity,
            )
        if row is None:
            raise HTTPException(status_code=409, detail="Insufficient stock")
        return {"item_id": item_id, "remaining_stock": row["stock"]}

    stock = STOCK_DB.get(item_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if stock < quantity:
        raise HTTPException(status_code=409, detail="Insufficient stock")
    STOCK_DB[item_id] -= quantity
    return {"item_id": item_id, "remaining_stock": STOCK_DB[item_id]}


@app.post("/release")
async def release_stock(item_id: str, quantity: int, _user=Depends(require_user)):
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")

    pool = getattr(app.state, "db", None)
    if pool:
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE inventory
                SET stock = stock + $2
                WHERE item_id=$1
                RETURNING stock
                """,
                item_id,
                quantity,
            )
        if row is None:
            raise HTTPException(status_code=404, detail="Item not found")
        return {"item_id": item_id, "remaining_stock": row["stock"]}

    if item_id not in STOCK_DB:
        raise HTTPException(status_code=404, detail="Item not found")
    STOCK_DB[item_id] += quantity
    return {"item_id": item_id, "remaining_stock": STOCK_DB[item_id]}

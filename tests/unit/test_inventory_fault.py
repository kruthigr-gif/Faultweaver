import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient


SERVICES_DIR = Path(__file__).resolve().parents[2] / "services"
sys.path.insert(0, str(SERVICES_DIR))
from inventory_service import app as inventory_app


def test_injected_inventory_error_fails_order_reservation(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(inventory_app, "FORCE_ERROR", "500")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with TestClient(inventory_app.app) as client:
        response = client.post(
            "/reserve",
            params={"item_id": "itemA", "quantity": 1},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Injected fault"


def test_injected_latency_delays_order_reservation(monkeypatch):
    delays = []
    initial_stock = inventory_app.STOCK_DB["itemA"]

    async def record_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(inventory_app, "LATENCY_MS", 1500)
    monkeypatch.setattr(inventory_app.asyncio, "sleep", record_sleep)

    try:
        asyncio.run(
            inventory_app.reserve_stock(
                item_id="itemA",
                quantity=1,
                _user={"sub": "local-test"},
            )
        )
    finally:
        inventory_app.STOCK_DB["itemA"] = initial_stock

    assert delays == [1.5]

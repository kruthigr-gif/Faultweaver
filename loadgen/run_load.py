import argparse
import asyncio
import math
import random
import time
from collections import Counter
from pathlib import Path

import httpx


TOKEN_URL = "http://127.0.0.1:8081/realms/sre-local/protocol/openid-connect/token"


def get_local_token() -> dict:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    username = values.get("SRE_LOCAL_USERNAME")
    password = values.get("SRE_LOCAL_PASSWORD")
    if not username or not password:
        raise RuntimeError("Run scripts/start_local.py first to create local API credentials")

    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "sre-api",
            "username": username,
            "password": password,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


class TokenProvider:
    def __init__(self, token_data: dict, client: httpx.AsyncClient):
        self.access_token = token_data["access_token"]
        self.refresh_token = token_data["refresh_token"]
        self.expires_at = time.monotonic() + token_data["expires_in"]
        self.client = client
        self.lock = asyncio.Lock()

    async def get_access_token(self) -> str:
        if time.monotonic() >= self.expires_at - 30:
            async with self.lock:
                if time.monotonic() >= self.expires_at - 30:
                    response = await self.client.post(
                        TOKEN_URL,
                        data={
                            "grant_type": "refresh_token",
                            "client_id": "sre-api",
                            "refresh_token": self.refresh_token,
                        },
                    )
                    response.raise_for_status()
                    token_data = response.json()
                    self.access_token = token_data["access_token"]
                    self.refresh_token = token_data.get(
                        "refresh_token", self.refresh_token
                    )
                    self.expires_at = time.monotonic() + token_data["expires_in"]
        return self.access_token


async def send_order(
    client: httpx.AsyncClient,
    base_url: str,
    item_id: str,
    quantity: int,
    token_provider: TokenProvider,
) -> str:
    try:
        token = await token_provider.get_access_token()
        response = await client.post(
            f"{base_url}/orders",
            params={"item_id": item_id, "quantity": quantity},
            headers={"Authorization": f"Bearer {token}"},
        )
        return str(response.status_code)
    except httpx.RequestError:
        return "request_error"


async def worker(
    worker_id: int,
    rps: float,
    duration: int,
    seed: int,
    base_url: str,
    token_provider: TokenProvider,
) -> Counter:
    rng = random.Random(seed + worker_id)
    interval = 1.0 / rps
    deadline = time.monotonic() + duration
    next_request = time.monotonic()
    results = Counter()

    async with httpx.AsyncClient(timeout=10) as client:
        while next_request < deadline:
            result = await send_order(
                client,
                base_url,
                rng.choice(["itemA", "itemB"]),
                rng.randint(1, 5),
                token_provider,
            )
            results[result] += 1
            next_request += interval
            await asyncio.sleep(max(0, next_request - time.monotonic()))

    return results


async def run_load(
    rps: float,
    duration: int,
    workers: int,
    seed: int,
    base_url: str,
) -> Counter:
    if not math.isfinite(rps) or rps <= 0 or duration <= 0 or workers <= 0:
        raise ValueError("rps, duration, and workers must all be positive")

    token_data = get_local_token()
    async with httpx.AsyncClient(timeout=10) as token_client:
        token_provider = TokenProvider(token_data, token_client)
        results = await asyncio.gather(
            *(
                worker(index, rps, duration, seed, base_url, token_provider)
                for index in range(workers)
            )
        )
    return sum(results, Counter())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate order-service traffic")
    parser.add_argument("--rps", type=float, default=5, help="Requests per second per worker")
    parser.add_argument("--duration", type=int, default=60, help="Duration in seconds")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent workers")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--url", type=str, default="http://localhost:8000", help="Order-service URL")
    args = parser.parse_args()

    try:
        results = asyncio.run(
            run_load(args.rps, args.duration, args.workers, args.seed, args.url)
        )
    except ValueError as exc:
        parser.error(str(exc))

    requests_sent = sum(results.values())
    successes = sum(
        count
        for status, count in results.items()
        if status.isdigit() and int(status) < 400
    )
    failures = requests_sent - successes
    print(f"Requests: {requests_sent}; successful: {successes}; failed: {failures}")
    if results:
        print("HTTP results: " + ", ".join(
            f"{status}={count}" for status, count in sorted(results.items())
        ))
    if not successes:
        raise SystemExit("No requests succeeded; check the order-service URL and logs.")

from datetime import timezone

import httpx
from fastapi import HTTPException

from .models import IncidentRequest, InvestigationReport


class InvestigatorClient:
    def __init__(self, base_url: str, timeout_seconds: float = 15):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=3),
        )

    async def investigate(
        self,
        request: IncidentRequest,
        authorization: str | None,
    ) -> InvestigationReport:
        headers = {"Authorization": authorization} if authorization else {}
        alert_time = request.alert_time
        if alert_time.tzinfo is None:
            alert_time = alert_time.replace(tzinfo=timezone.utc)
        else:
            alert_time = alert_time.astimezone(timezone.utc)

        try:
            response = await self.client.post(
                "/investigate",
                json={
                    "alert_time": alert_time.isoformat().replace("+00:00", "Z"),
                    "service": request.service,
                    "window_seconds": request.window_seconds,
                },
                headers=headers,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = 503 if exc.response.status_code >= 500 else exc.response.status_code
            raise HTTPException(
                status_code=status_code,
                detail="Investigator rejected or could not complete the incident request.",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail="Investigator is unavailable; no incident was recorded.",
            ) from exc

        return InvestigationReport.model_validate(response.json())

    async def close(self):
        await self.client.aclose()

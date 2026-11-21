import os
import time
from functools import lru_cache

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt


bearer_scheme = HTTPBearer(auto_error=False)


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").lower() in {"1", "true", "yes"}


@lru_cache(maxsize=1)
def _jwks(jwks_url: str, cache_key: int) -> dict:
    response = httpx.get(jwks_url, timeout=5)
    response.raise_for_status()
    return response.json()


async def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if not auth_enabled():
        return None

    if credentials is None:
        raise HTTPException(status_code=401, detail="Bearer access token required")

    issuer = os.getenv("OIDC_ISSUER")
    audience = os.getenv("OIDC_AUDIENCE")
    jwks_url = os.getenv("OIDC_JWKS_URL")
    if not issuer or not audience or not jwks_url:
        raise HTTPException(status_code=500, detail="OIDC configuration is incomplete")

    token = credentials.credentials
    try:
        key_set = _jwks(jwks_url, int(time.time() // 300))
        claims = jwt.decode(
            token,
            key_set,
            algorithms=[os.getenv("OIDC_ALGORITHM", "RS256")],
            audience=audience,
            issuer=issuer,
        )
        return claims
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc

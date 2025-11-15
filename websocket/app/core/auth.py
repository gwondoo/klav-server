from typing import Optional

import httpx

from .config import settings


class AuthError(Exception):
    pass


async def get_user_from_django(access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{settings.DJANGO_BASE_URL}/api/auth/me/"

    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code != 200:
        raise AuthError("Invalid token or user not found.")

    return resp.json()

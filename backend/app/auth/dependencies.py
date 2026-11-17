from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from supabase import AuthApiError

from app.database.supabase import (
    create_client_user,
    normalize_token,
)

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    id: UUID
    email: str | None = None


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    if credentials is None:
        raise unauthorized()
    return normalize_token(credentials.credentials)


async def verify_token(token: str, http_client: httpx.AsyncClient) -> CurrentUser:
    try:
        client = await create_client_user(token, http_client)
        response = await client.auth.get_user(token)
    except AuthApiError as exc:
        if exc.status >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service unavailable",
            ) from exc
        raise unauthorized() from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc

    if response is None or response.user is None:
        raise unauthorized()
    return CurrentUser.model_validate(response.user.model_dump())


async def get_current_user(
    request: Request,
    token: str = Depends(get_token),
) -> CurrentUser:
    return await verify_token(token, request.app.state.supabase_http_client)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

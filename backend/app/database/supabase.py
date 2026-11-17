import httpx
from supabase import AsyncClient, AsyncClientOptions, create_async_client

from app.config import settings


def server_client_options(
    http_client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
) -> AsyncClientOptions:
    options = AsyncClientOptions(
        auto_refresh_token=False,
        persist_session=False,
        httpx_client=http_client,
    )
    if headers is not None:
        options.headers.update(headers)
    return options


def normalize_token(token: str) -> str:
    parts = token.strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return token.strip()


async def service_client_role(http_client: httpx.AsyncClient) -> AsyncClient:
    return await create_async_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_SERVICE_ROLE_KEY,
        options=server_client_options(http_client),
    )


async def create_client_user(
    access_token: str,
    http_client: httpx.AsyncClient,
) -> AsyncClient:
    token = normalize_token(access_token)
    return await create_async_client(
        settings.SUPABASE_URL,
        settings.SUPABASE_ANON_KEY,
        options=server_client_options(
            http_client,
            headers={"Authorization": f"Bearer {token}"},
        ),
    )

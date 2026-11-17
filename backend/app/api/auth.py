import httpx
from fastapi import APIRouter, HTTPException, Request, status
from supabase import PostgrestAPIError

from app.auth.dependencies import CurrentUser, CurrentUserDep
from app.database.supabase import service_client_role

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=CurrentUser)
async def read_current_user(
    current_user: CurrentUserDep,
    request: Request,
) -> CurrentUser:
    client = await service_client_role(request.app.state.supabase_http_client)
    try:
        await (
            client.table("users")
            .upsert(
                {"id": str(current_user.id), "email": current_user.email},
                on_conflict="id",
            )
            .execute()
        )
    except (PostgrestAPIError, httpx.HTTPError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to synchronize user profile",
        ) from exc
    return current_user

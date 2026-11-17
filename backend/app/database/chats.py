from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from supabase import AsyncClient

from app.chat.orchestrator import HistoryMessage


async def list_threads(client: AsyncClient, user_id: UUID) -> list[dict[str, Any]]:
    response = await (
        client.table("chat_threads")
        .select("id,user_id,title,created_at,updated_at")
        .eq("user_id", str(user_id))
        .order("updated_at", desc=True)
        .execute()
    )
    return cast(list[dict[str, Any]], response.data)


async def get_thread(client: AsyncClient, thread_id: UUID) -> dict[str, Any] | None:
    response = await (
        client.table("chat_threads")
        .select("id,user_id,title,created_at,updated_at")
        .eq("id", str(thread_id))
        .maybe_single()
        .execute()
    )

    if response is None:
        return None

    return cast(dict[str, Any], response.data)


async def create_thread(
    client: AsyncClient, user_id: UUID, title: str
) -> dict[str, Any]:
    response = await (
        client.table("chat_threads")
        .insert({"id": str(uuid4()), "user_id": str(user_id), "title": title})
        .select("id,user_id,title,created_at,updated_at")
        .single()
        .execute()
    )

    return cast(dict[str, Any], response.data)


async def ensure_user(client: AsyncClient, user_id: UUID, email: str | None) -> None:
    await (
        client.table("users")
        .upsert({"id": str(user_id), "email": email}, on_conflict="id")
        .execute()
    )


async def rename_thread(
    client: AsyncClient, thread_id: UUID, title: str
) -> dict[str, Any]:
    response = await (
        client.table("chat_threads")
        .update({"title": title, "updated_at": datetime.now(UTC).isoformat()})
        .eq("id", str(thread_id))
        .select("id,user_id,title,created_at,updated_at")
        .single()
        .execute()
    )
    return cast(dict[str, Any], response.data)


async def delete_thread(client: AsyncClient, thread_id: UUID) -> None:
    await client.table("chat_threads").delete().eq("id", str(thread_id)).execute()


async def list_messages(client: AsyncClient, thread_id: UUID) -> list[dict[str, Any]]:
    response = await (
        client.table("chat_messages")
        .select("id,thread_id,role,content,parts,created_at")
        .eq("thread_id", str(thread_id))
        .order("created_at")
        .execute()
    )
    return cast(list[dict[str, Any]], response.data)


async def list_recent_messages(
    client: AsyncClient, thread_id: UUID, limit: int
) -> list[HistoryMessage]:
    response = await (
        client.table("chat_messages")
        .select("id,thread_id,role,content,parts,created_at")
        .eq("thread_id", str(thread_id))
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    messages = cast(list[HistoryMessage], response.data)
    messages.reverse()
    return messages


async def create_message(
    client: AsyncClient,
    thread_id: UUID,
    role: str,
    content: str,
) -> dict[str, Any]:
    response = await (
        client.table("chat_messages")
        .insert(
            {
                "id": str(uuid4()),
                "thread_id": str(thread_id),
                "role": role,
                "content": content,
            }
        )
        .select("id,thread_id,role,content,parts,created_at")
        .single()
        .execute()
    )
    return cast(dict[str, Any], response.data)


async def touch_thread(client: AsyncClient, thread_id: UUID) -> None:
    await (
        client.table("chat_threads")
        .update({"updated_at": datetime.now(UTC).isoformat()})
        .eq("id", str(thread_id))
        .execute()
    )

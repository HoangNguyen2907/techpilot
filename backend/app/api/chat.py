import asyncio
import logging
from collections.abc import AsyncGenerator
from io import StringIO
from typing import Annotated, Any
from uuid import UUID

import httpx
from app.auth.dependencies import CurrentUserDep
from app.chat.orchestrator import (
    HistoryMessage,
    InputExceedsContext,
    fit_history,
    stream_answer,
    title_from_content,
)
from app.chat.streaming import (
    ClosingStreamingResponse,
    format_sse_event,
    with_heartbeats,
)
from app.config import settings
from app.database import chats
from app.database.supabase import service_client_role
from fastapi import APIRouter, HTTPException, Request, status
from openai import OpenAIError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from supabase import AsyncClient, PostgrestAPIError

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)
Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
MessageContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)
]


class ThreadRename(BaseModel):
    title: Title


class ThreadSummary(BaseModel):
    id: UUID
    title: str
    created_at: str
    updated_at: str


class ChatMessage(BaseModel):
    id: UUID
    thread_id: UUID
    role: str
    content: str
    parts: Any = None
    created_at: str


class ThreadDetail(ThreadSummary):
    messages: list[ChatMessage]


class StreamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    thread_id: UUID | None = Field(alias="threadId")
    content: MessageContent


def thread_access(thread: dict[str, Any] | None, user_id: UUID) -> None:
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat thread not found")
    if thread["user_id"] != str(user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")


async def get_service_client(request: Request) -> AsyncClient:
    return await service_client_role(request.app.state.supabase_http_client)


@router.get("/threads", response_model=list[ThreadSummary])
async def read_threads(current_user: CurrentUserDep, request: Request):
    client = await get_service_client(request)
    try:
        return await chats.list_threads(client, current_user.id)
    except (PostgrestAPIError, httpx.HTTPError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Chat storage unavailable"
        ) from exc


@router.get("/threads/{thread_id}", response_model=ThreadDetail)
async def read_thread(thread_id: UUID, current_user: CurrentUserDep, request: Request):
    client = await get_service_client(request)
    try:
        thread = await chats.get_thread(client, thread_id)

        if not thread:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Thread not found")

        thread_access(thread, current_user.id)
        messages = await chats.list_messages(client, thread_id)

        return {**thread, "messages": messages}

    except HTTPException:
        raise
    except (PostgrestAPIError, httpx.HTTPError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Chat storage unavailable"
        ) from exc


@router.patch("/threads/{thread_id}", response_model=ThreadSummary)
async def update_thread(
    thread_id: UUID,
    body: ThreadRename,
    current_user: CurrentUserDep,
    request: Request,
):
    client = await get_service_client(request)
    try:
        thread = await chats.get_thread(client, thread_id)

        if not thread:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Thread not found")

        thread_access(thread, current_user.id)
        return await chats.rename_thread(client, thread_id, body.title)
    except HTTPException:
        raise
    except (PostgrestAPIError, httpx.HTTPError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Chat storage unavailable"
        ) from exc


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_thread(
    thread_id: UUID, current_user: CurrentUserDep, request: Request
):
    client = await get_service_client(request)
    try:
        thread_access(await chats.get_thread(client, thread_id), current_user.id)
        await chats.delete_thread(client, thread_id)
    except HTTPException:
        raise
    except (PostgrestAPIError, httpx.HTTPError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Chat storage unavailable"
        ) from exc


@router.post("/stream")
async def stream_chat(
    body: StreamRequest, current_user: CurrentUserDep, request: Request
):
    database = await get_service_client(request)
    if body.thread_id is None:
        thread = None
        history: list[HistoryMessage] = []
    else:
        try:
            thread = await chats.get_thread(database, body.thread_id)
            thread_access(thread, current_user.id)
            history = await chats.list_recent_messages(
                database,
                body.thread_id,
                settings.OPENAI_MAX_HISTORY_MESSAGES,
            )
        except HTTPException:
            raise
        except (PostgrestAPIError, httpx.HTTPError) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Chat storage unavailable"
            ) from exc

    try:
        history = fit_history(
            history,
            body.content,
            settings.OPENAI_CONTEXT_WINDOW_TOKENS,
            settings.OPENAI_MAX_COMPLETION_TOKENS,
        )
    except InputExceedsContext as exc:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "The message exceeds the available model context window.",
        ) from exc

    async def events() -> AsyncGenerator[str, None]:
        active_thread = thread
        try:
            # StreamingResponse propagates an ASGI disconnect as task cancellation.
            # This first comment flushes headers while setup and model work proceed.
            yield ": connected\n\n"
            if active_thread is None:
                title = title_from_content(body.content)
                await chats.ensure_user(database, current_user.id, current_user.email)
                active_thread = await chats.create_thread(
                    database, current_user.id, title
                )
                yield format_sse_event(
                    "thread_created",
                    {"threadId": active_thread["id"], "title": title},
                )

            thread_id = UUID(str(active_thread["id"]))
            await chats.create_message(database, thread_id, "user", body.content)
            await chats.touch_thread(database, thread_id)
            answer = StringIO()
            answer_characters = 0
            max_answer_characters = settings.OPENAI_MAX_COMPLETION_TOKENS * 8
            finish_reason = "unknown"
            async for part in stream_answer(
                request.app.state.openai_client,
                settings.OPENAI_CHAT_MODEL,
                history,
                body.content,
                settings.OPENAI_MAX_COMPLETION_TOKENS,
            ):
                if part.text:
                    answer_characters += len(part.text)
                    if answer_characters > max_answer_characters:
                        raise RuntimeError("Model output exceeded its configured limit")
                    answer.write(part.text)
                    yield format_sse_event("delta", {"text": part.text})
                if part.finish_reason:
                    finish_reason = part.finish_reason

            await chats.create_message(
                database, thread_id, "assistant", answer.getvalue()
            )
            await chats.touch_thread(database, thread_id)
            yield format_sse_event("done", {"finishReason": finish_reason})
        except asyncio.CancelledError:
            logger.info(
                "Chat stream cancelled",
                extra={
                    "thread_id": (
                        str(active_thread["id"]) if active_thread is not None else None
                    )
                },
            )
            raise
        except (OpenAIError, PostgrestAPIError, httpx.HTTPError):
            logger.exception("Chat provider or persistence request failed")
            yield format_sse_event(
                "error", {"error": "Unable to complete this chat turn."}
            )
        except Exception:
            logger.exception("Chat turn failed")
            yield format_sse_event(
                "error", {"error": "Unable to complete this chat turn."}
            )

    return ClosingStreamingResponse(
        with_heartbeats(events()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

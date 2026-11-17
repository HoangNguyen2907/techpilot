import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.api import chat
from app.auth.dependencies import CurrentUser
from app.chat.orchestrator import StreamPart

USER_ID = UUID("c2f41861-07b1-43c2-ae0d-3c6a5ef26fc2")
OTHER_USER_ID = UUID("ac50fa89-c176-48c9-9382-bfb14585db7b")
THREAD_ID = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
CURRENT_USER = CurrentUser(id=USER_ID)


def run(coroutine):
    return asyncio.run(coroutine)


def request() -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                supabase_http_client=object(),
                openai_client=object(),
            )
        )
    )


def test_thread_access_hides_missing_threads_and_forbids_other_owners():
    with pytest.raises(HTTPException) as missing:
        chat.thread_access(None, USER_ID)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as forbidden:
        chat.thread_access({"user_id": str(OTHER_USER_ID)}, USER_ID)
    assert forbidden.value.status_code == 403


def test_sse_event_uses_standard_event_and_json_data_lines():
    result = chat.format_sse_event("delta", {"text": "hello"})
    assert result == 'event: delta\ndata: {"text":"hello"}\n\n'
    assert json.loads(result.split("data: ", 1)[1]) == {"text": "hello"}


def test_new_chat_stream_persists_both_messages_and_emits_events(monkeypatch):
    saved_messages = []
    touched_threads = []
    created_thread = {
        "id": str(THREAD_ID),
        "user_id": str(USER_ID),
        "title": "Bun versus Node?",
    }

    async def create_thread(_client, user_id, title):
        assert user_id == USER_ID
        assert title == created_thread["title"]
        return created_thread

    async def ensure_user(_client, user_id, email):
        assert user_id == USER_ID
        assert email is None

    async def create_message(_client, thread_id, role, content):
        saved_messages.append((thread_id, role, content))

    async def touch_thread(_client, thread_id):
        touched_threads.append(thread_id)

    async def stream_answer(*_args):
        yield StreamPart(text="Bun is")
        yield StreamPart(text=" discussed.")
        yield StreamPart(finish_reason="stop")

    async def get_client(_request):
        return object()

    monkeypatch.setattr(chat.chats, "create_thread", create_thread)
    monkeypatch.setattr(chat.chats, "ensure_user", ensure_user)
    monkeypatch.setattr(chat.chats, "create_message", create_message)
    monkeypatch.setattr(chat.chats, "touch_thread", touch_thread)
    monkeypatch.setattr(chat, "stream_answer", stream_answer)
    monkeypatch.setattr(chat, "get_service_client", get_client)

    response = run(
        chat.stream_chat(
            chat.StreamRequest(threadId=None, content="Bun versus Node?"),
            CURRENT_USER,
            request(),
        )
    )

    async def consume():
        return "".join([chunk async for chunk in response.body_iterator])

    result = run(consume())
    assert "event: thread_created\ndata:" in result
    assert 'event: delta\ndata: {"text":"Bun is"}' in result
    assert 'event: done\ndata: {"finishReason":"stop"}' in result
    assert saved_messages == [
        (THREAD_ID, "user", "Bun versus Node?"),
        (THREAD_ID, "assistant", "Bun is discussed."),
    ]
    assert touched_threads == [THREAD_ID]


def test_existing_thread_loads_history_and_rejects_another_owner(monkeypatch):
    async def get_client(_request):
        return object()

    async def get_thread(*_args):
        return {"id": str(THREAD_ID), "user_id": str(OTHER_USER_ID)}

    monkeypatch.setattr(chat, "get_service_client", get_client)
    monkeypatch.setattr(chat.chats, "get_thread", get_thread)
    with pytest.raises(HTTPException) as forbidden:
        run(
            chat.stream_chat(
                chat.StreamRequest(threadId=THREAD_ID, content="Follow up"),
                CURRENT_USER,
                request(),
            )
        )
    assert forbidden.value.status_code == 403


def test_existing_thread_uses_database_history(monkeypatch):
    saved_messages = []
    contexts = []
    history = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]

    async def get_client(_request):
        return object()

    async def get_thread(_client, thread_id):
        assert thread_id == THREAD_ID
        return {"id": str(THREAD_ID), "user_id": str(USER_ID)}

    async def list_messages(_client, thread_id, _limit):
        assert thread_id == THREAD_ID
        return history

    async def create_message(_client, thread_id, role, content):
        saved_messages.append((thread_id, role, content))

    async def touch_thread(*_args):
        return None

    async def stream_answer(_client, _model, turns, content, _max_tokens):
        contexts.append((turns, content))
        yield StreamPart(text="Next answer")
        yield StreamPart(finish_reason="stop")

    monkeypatch.setattr(chat, "get_service_client", get_client)
    monkeypatch.setattr(chat.chats, "get_thread", get_thread)
    monkeypatch.setattr(chat.chats, "list_recent_messages", list_messages)
    monkeypatch.setattr(chat.chats, "create_message", create_message)
    monkeypatch.setattr(chat.chats, "touch_thread", touch_thread)
    monkeypatch.setattr(chat, "stream_answer", stream_answer)

    response = run(
        chat.stream_chat(
            chat.StreamRequest(threadId=THREAD_ID, content="Follow-up question"),
            CURRENT_USER,
            request(),
        )
    )

    async def consume():
        return "".join([chunk async for chunk in response.body_iterator])

    result = run(consume())
    assert "event: thread_created" not in result
    assert contexts == [(history, "Follow-up question")]
    assert saved_messages == [
        (THREAD_ID, "user", "Follow-up question"),
        (THREAD_ID, "assistant", "Next answer"),
    ]

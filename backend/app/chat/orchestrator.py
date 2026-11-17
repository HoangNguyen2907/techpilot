from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, TypedDict

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam


class HistoryMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class StreamPart:
    text: str | None = None
    finish_reason: str | None = None


class InputExceedsContext(Exception):
    """Raised when the newest user message cannot fit the configured context."""


def estimate_tokens(text: str) -> int:
    # One token per byte is conservative and avoids a model-specific tokenizer dependency.
    return len(text.encode("utf-8"))


def fit_history(
    history: list[HistoryMessage],
    content: str,
    context_window_tokens: int,
    max_completion_tokens: int,
) -> list[HistoryMessage]:
    input_budget = context_window_tokens - max_completion_tokens - 64
    current_cost = estimate_tokens(content) + 4
    if input_budget <= 0 or current_cost > input_budget:
        raise InputExceedsContext

    selected: list[HistoryMessage] = []
    remaining = input_budget - current_cost
    for message in reversed(history):
        role = message.get("role")
        text = message.get("content", "")
        if role not in {"user", "assistant"}:
            continue
        message_cost = estimate_tokens(text) + 4
        if message_cost > remaining:
            break
        selected.append({"role": role, "content": text})
        remaining -= message_cost
    selected.reverse()
    return selected


def title_from_content(content: str) -> str:
    words = content.split()[:6]
    if len(words) < 3:
        words.extend(["Chat"] * (3 - len(words)))
    return " ".join(words[:6])[:255]


async def stream_answer(
    client: AsyncOpenAI,
    model: str,
    history: list[HistoryMessage],
    content: str,
    max_completion_tokens: int,
) -> AsyncIterator[StreamPart]:
    messages: list[ChatCompletionMessageParam] = []

    for row in history:
        if row["role"] == "user":
            message: ChatCompletionMessageParam = {
                "role": "user",
                "content": row["content"],
            }
        else:
            message = {
                "role": "assistant",
                "content": row["content"],
            }

        messages.append(message)
    messages.append({"role": "user", "content": content})
    async with client.chat.completions.stream(
        model=model,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
    ) as stream:
        async for event in stream:
            if event.type == "content.delta" or event.type == "refusal.delta":
                yield StreamPart(text=event.delta)
        completion = await stream.get_final_completion()
        finish_reason = (
            completion.choices[0].finish_reason if completion.choices else None
        )
        yield StreamPart(finish_reason=finish_reason or "unknown")

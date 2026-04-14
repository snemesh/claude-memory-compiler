"""Thin synchronous wrapper around claude_agent_sdk.query().

The SDK is async and streams messages; callers here want a simple
synchronous (prompt → text+cost) function. Extracted so tests can
monkeypatch `_run_llm` without touching the SDK at all.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

_RETRY_DELAYS_S = (5, 15, 45)  # 3 attempts, totals ~65s max wait
_RETRYABLE_PATTERN = re.compile(
    r"rate[\s_-]?limit|429|too many requests|command failed with exit code",
    re.IGNORECASE,
)


def _is_retryable(exc: BaseException) -> bool:
    """Heuristic: retry on rate-limit and transient SDK 'Command failed' errors.

    The Agent SDK wraps upstream 429s in a generic 'Command failed with exit
    code N' exception, so we treat that pattern as retryable too. Other
    exceptions (network errors, auth errors) are not retried.
    """
    msg = str(exc)
    return bool(_RETRYABLE_PATTERN.search(msg))


@dataclass(frozen=True)
class LLMResponse:
    text: str
    cost_usd: float
    model: str


_DEFAULT_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]


async def _run_llm(
    prompt: str,
    model: str,
    cwd: Path,
    max_turns: int,
    allowed_tools: list[str] | None = None,
) -> LLMResponse:
    """Call the Agent SDK, stream messages, return consolidated response.

    `allowed_tools=None` enables the default Pass-1 toolset
    (Read/Write/Edit/Glob/Grep). Pass an empty list for tool-less single-turn
    calls (e.g. Pass 3 Haiku validation).

    Tests monkeypatch this function; don't add logic above the SDK call.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    tools = _DEFAULT_TOOLS if allowed_tools is None else allowed_tools
    text_parts: list[str] = []
    cost = 0.0

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(cwd),
            system_prompt={"type": "preset", "preset": "claude_code"},
            allowed_tools=tools,
            permission_mode="acceptEdits",
            max_turns=max_turns,
            model=model,
            add_dirs=[str(cwd)],
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0

    return LLMResponse(text="\n".join(text_parts), cost_usd=cost, model=model)


async def _run_llm_with_retry(
    prompt: str,
    model: str,
    cwd: Path,
    max_turns: int,
    allowed_tools: list[str] | None,
) -> LLMResponse:
    """Wrap _run_llm with exponential-backoff retry for transient errors."""
    last_exc: BaseException | None = None
    for attempt, delay in enumerate((0, *_RETRY_DELAYS_S)):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await _run_llm(prompt, model, cwd, max_turns, allowed_tools)
        except BaseException as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            continue
    assert last_exc is not None
    raise last_exc


def call_llm(
    prompt: str,
    model: str,
    cwd: Path,
    max_turns: int = 30,
    allowed_tools: list[str] | None = None,
) -> LLMResponse:
    """Synchronous entry point. Runs the async SDK call with retry wrapper."""
    return asyncio.run(
        _run_llm_with_retry(prompt, model, cwd, max_turns, allowed_tools)
    )

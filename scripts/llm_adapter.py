"""Thin synchronous wrapper around claude_agent_sdk.query().

The SDK is async and streams messages; callers here want a simple
synchronous (prompt → text+cost) function. Extracted so tests can
monkeypatch `_run_llm` without touching the SDK at all.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LLMResponse:
    text: str
    cost_usd: float
    model: str


async def _run_llm(
    prompt: str,
    model: str,
    cwd: Path,
    max_turns: int,
) -> LLMResponse:
    """Call the Agent SDK, stream messages, return consolidated response.

    Tests monkeypatch this function; don't add logic above the SDK call.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    text_parts: list[str] = []
    cost = 0.0

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(cwd),
            system_prompt={"type": "preset", "preset": "claude_code"},
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep"],
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


def call_llm(
    prompt: str,
    model: str,
    cwd: Path,
    max_turns: int = 30,
) -> LLMResponse:
    """Synchronous entry point. Runs the async SDK call in a fresh event loop."""
    return asyncio.run(_run_llm(prompt, model, cwd, max_turns))

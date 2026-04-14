"""Tests for llm_adapter.py — LLM wrapper that tests can monkeypatch."""
import pytest

from llm_adapter import LLMResponse, call_llm


def test_llm_response_is_immutable():
    r = LLMResponse(text="hello", cost_usd=0.12, model="claude-sonnet-4-6")
    with pytest.raises(Exception):
        r.text = "changed"  # frozen dataclass


def test_call_llm_dispatches_via_run_llm_fn(monkeypatch, tmp_path):
    """call_llm goes through _run_llm (monkeypatchable) rather than the SDK directly."""
    captured = {}

    async def fake_run(prompt, model, cwd, max_turns, allowed_tools=None):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["cwd"] = cwd
        captured["allowed_tools"] = allowed_tools
        return LLMResponse(text="FAKE", cost_usd=0.05, model=model)

    monkeypatch.setattr("llm_adapter._run_llm", fake_run)

    result = call_llm(
        prompt="compile clubs",
        model="claude-sonnet-4-6",
        cwd=tmp_path,
        max_turns=30,
    )
    assert result.text == "FAKE"
    assert result.cost_usd == 0.05
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["prompt"] == "compile clubs"


def test_call_llm_passes_explicit_empty_tool_list(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(prompt, model, cwd, max_turns, allowed_tools=None):
        captured["allowed_tools"] = allowed_tools
        return LLMResponse(text="", cost_usd=0.0, model=model)

    monkeypatch.setattr("llm_adapter._run_llm", fake_run)

    call_llm("p", "claude-haiku-4-5", tmp_path, max_turns=1, allowed_tools=[])
    assert captured["allowed_tools"] == []


def test_is_retryable_matches_known_patterns():
    from llm_adapter import _is_retryable
    assert _is_retryable(Exception("Rate limit reached")) is True
    assert _is_retryable(Exception("429 Too Many Requests")) is True
    assert _is_retryable(Exception("Command failed with exit code 1")) is True
    assert _is_retryable(Exception("Permission denied")) is False
    assert _is_retryable(Exception("Invalid API key")) is False


def test_call_llm_retries_on_retryable_error(monkeypatch, tmp_path):
    attempts = {"n": 0}

    async def fake_run(prompt, model, cwd, max_turns, allowed_tools=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Exception("Command failed with exit code 1")
        return LLMResponse(text="recovered", cost_usd=0.01, model=model)

    monkeypatch.setattr("llm_adapter._run_llm", fake_run)
    # Neutralise the backoff sleeps so tests don't actually wait.
    monkeypatch.setattr("llm_adapter.asyncio.sleep",
                        lambda s: asyncio_sleep_stub())

    async def asyncio_sleep_stub(*args, **kwargs):
        pass

    # Monkeypatch again with actual coroutine-returning stub
    import asyncio as _asyncio
    async def _fast(delay):
        return None
    monkeypatch.setattr("llm_adapter.asyncio.sleep", _fast)

    result = call_llm("p", "claude-sonnet-4-6", tmp_path, max_turns=1)
    assert result.text == "recovered"
    assert attempts["n"] == 3


def test_call_llm_raises_on_non_retryable_error(monkeypatch, tmp_path):
    async def fake_run(prompt, model, cwd, max_turns, allowed_tools=None):
        raise Exception("Permission denied")

    monkeypatch.setattr("llm_adapter._run_llm", fake_run)

    with pytest.raises(Exception, match="Permission denied"):
        call_llm("p", "claude-sonnet-4-6", tmp_path, max_turns=1)


def test_call_llm_gives_up_after_max_retries(monkeypatch, tmp_path):
    async def always_fail(prompt, model, cwd, max_turns, allowed_tools=None):
        raise Exception("Rate limit hit")

    monkeypatch.setattr("llm_adapter._run_llm", always_fail)

    async def _fast(delay):
        return None
    monkeypatch.setattr("llm_adapter.asyncio.sleep", _fast)

    with pytest.raises(Exception, match="Rate limit hit"):
        call_llm("p", "claude-sonnet-4-6", tmp_path, max_turns=1)

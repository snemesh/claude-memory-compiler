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

    async def fake_run(prompt, model, cwd, max_turns):
        captured["prompt"] = prompt
        captured["model"] = model
        captured["cwd"] = cwd
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

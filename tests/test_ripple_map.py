"""Tests for ripple_map.py — manifest of non-obvious cross-service deps."""
from pathlib import Path

import pytest

from ripple_map import RippleRule, expand_ripple, load_ripple_map


def test_load_ripple_map_parses_yaml(tmp_path):
    path = tmp_path / "ripple.yaml"
    path.write_text(
        """
patterns:
  - match: "proto/clubs/**"
    revalidate: [api-gateway, club-finance, referrals]
  - match: "services/auth-service/**"
    revalidate: [fan-registration, club-self-registration]
""",
        encoding="utf-8",
    )
    rules = load_ripple_map(path)
    assert len(rules) == 2
    assert rules[0].match == "proto/clubs/**"
    assert rules[0].revalidate == ["api-gateway", "club-finance", "referrals"]


def test_load_ripple_map_missing_file_returns_empty(tmp_path):
    assert load_ripple_map(tmp_path / "nonexistent.yaml") == []


def test_expand_ripple_matches_glob():
    rules = [
        RippleRule(match="proto/clubs/**", revalidate=["api-gateway", "club-finance"]),
        RippleRule(match="services/auth/**", revalidate=["fan-registration"]),
    ]
    affected = expand_ripple(rules, ["proto/clubs/v1/clubs.proto"])
    assert affected == {"api-gateway", "club-finance"}


def test_expand_ripple_multiple_patterns_match():
    rules = [
        RippleRule(match="proto/**", revalidate=["api-gateway"]),
        RippleRule(match="**/*.proto", revalidate=["content"]),
    ]
    affected = expand_ripple(rules, ["proto/clubs/v1/clubs.proto"])
    assert affected == {"api-gateway", "content"}


def test_expand_ripple_unmatched_files_empty():
    rules = [RippleRule(match="proto/**", revalidate=["api-gateway"])]
    assert expand_ripple(rules, ["README.md"]) == set()

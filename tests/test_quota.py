"""Tests for quota.py — subscription quota reader."""
from datetime import datetime, timezone

import pytest

from quota import QuotaSnapshot, find_oauth_token


def test_quota_snapshot_construction():
    snap = QuotaSnapshot(
        five_hour_used_pct=68.2,
        five_hour_resets_at=datetime(2026, 4, 14, 8, 15, tzinfo=timezone.utc),
        seven_day_used_pct=23.4,
        seven_day_resets_at=datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc),
        source="oauth",
    )
    assert snap.five_hour_used_pct == 68.2
    assert snap.source == "oauth"


def test_quota_snapshot_remaining_pct():
    snap = QuotaSnapshot(
        five_hour_used_pct=68.2,
        five_hour_resets_at=datetime(2026, 4, 14, 8, 15, tzinfo=timezone.utc),
        seven_day_used_pct=23.4,
        seven_day_resets_at=datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 4, 14, 3, 15, tzinfo=timezone.utc),
        source="oauth",
    )
    assert snap.five_hour_remaining_pct == pytest.approx(31.8)
    assert snap.seven_day_remaining_pct == pytest.approx(76.6)


import json


def test_find_oauth_token_from_credentials_file(tmp_path, monkeypatch):
    cred_file = tmp_path / ".credentials.json"
    cred_file.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-TESTTOKEN",
            "refreshToken": "x",
            "expiresAt": 9999999999999,
        }
    }))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("quota._CREDENTIALS_PATH", cred_file)

    token = find_oauth_token()
    assert token == "sk-ant-oat01-TESTTOKEN"


def test_find_oauth_token_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("quota._CREDENTIALS_PATH", tmp_path / "nonexistent.json")
    assert find_oauth_token() is None

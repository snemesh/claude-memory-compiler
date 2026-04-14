"""Tests for quota.py — subscription quota reader."""
from datetime import datetime, timezone

import pytest

from unittest.mock import MagicMock, patch

from quota import QuotaSnapshot, fetch_oauth_quota, find_oauth_token


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


def test_fetch_oauth_quota_parses_response():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "five_hour": {"used_percentage": 68.2, "resets_at": 1776412500},
        "seven_day": {"used_percentage": 23.4, "resets_at": 1776673500},
    }

    with patch("quota.requests.get", return_value=fake_response) as mock_get:
        snap = fetch_oauth_quota("sk-ant-oat01-TEST")

    assert snap is not None
    assert snap.five_hour_used_pct == 68.2
    assert snap.seven_day_used_pct == 23.4
    assert snap.source == "oauth"
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert "Bearer sk-ant-oat01-TEST" in call_args.kwargs["headers"]["Authorization"]


def test_fetch_oauth_quota_returns_none_on_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 401
    with patch("quota.requests.get", return_value=fake_response):
        snap = fetch_oauth_quota("bad-token")
    assert snap is None


def test_fetch_oauth_quota_returns_none_on_network_error():
    import requests
    with patch("quota.requests.get", side_effect=requests.ConnectionError("boom")):
        snap = fetch_oauth_quota("sk-ant-oat01-TEST")
    assert snap is None

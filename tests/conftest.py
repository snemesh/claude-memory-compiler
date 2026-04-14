"""Shared pytest fixtures."""
import sys
from pathlib import Path

import pytest

# Make scripts/ importable as a package-less module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture(autouse=True)
def _stub_get_quota(monkeypatch):
    """Default-stub quota lookups to None so tests don't scan the real
    ~/.claude/projects tree. Individual tests that need a real snapshot
    re-override this via monkeypatch.setattr on their target module.
    """
    # Patch on every module that imports get_quota from quota
    for mod in ("sync", "sync_loop", "quota"):
        try:
            monkeypatch.setattr(f"{mod}.get_quota", lambda: None, raising=False)
        except (AttributeError, ModuleNotFoundError):
            pass

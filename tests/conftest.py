"""Shared pytest fixtures."""
import sys
from pathlib import Path

# Make scripts/ importable as a package-less module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

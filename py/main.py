"""Vercel entrypoint for the api service. Works whether or not the project itself is installed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ft.api.app import app  # noqa: E402

__all__ = ["app"]

"""Pytest configuration: make the stub `homeassistant` package and the
integration itself importable, without installing anything."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "tests" / "stubs"))
sys.path.insert(0, str(ROOT / "custom_components"))

"""Provider SDKs stay outside the terminal startup path."""

from __future__ import annotations

import subprocess
import sys


def test_entry_point_import_does_not_load_provider_sdks() -> None:
    script = """
import sys
import super_agent.__main__
assert "openai" not in sys.modules
assert "anthropic" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_builtin_model_construction_does_not_load_provider_sdks() -> None:
    script = """
import sys
from super_agent import llm
llm.new_model("deepseek", llm.ProviderConfig(api_key="test", model="test"))
assert "openai" not in sys.modules
assert "anthropic" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True)

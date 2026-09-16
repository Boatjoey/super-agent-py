"""Fenced-code extraction, the source of the ``Ctrl+Y`` copy target.

The four cases cover the fence semantics: a fence is any line whose trimmed form
starts with ````` ``` `````, the opening line's language is dropped, and an
unterminated block contributes nothing.
"""

from __future__ import annotations

import pytest

from super_agent.tui import ExtractCodeBlocks

_CASES: tuple[tuple[str, str, list[str]], ...] = (
    ("no blocks", "hello world", []),
    ("one block", 'here is some code:\n```python\nprint("hi")\n```\nmore text', ['print("hi")']),
    ("multiple blocks", "```python\nprint(1)\n```\ntext\n```bash\necho 2\n```", ["print(1)", "echo 2"]),
    ("block with no language", "```\njust code\n```", ["just code"]),
    ("unterminated block is not a block", '```python\nprint("hi")', []),
    ("indented fence still opens", "  ```\n  indented\n  ```", ["  indented"]),
)


@pytest.mark.parametrize(("name", "content", "wanted"), _CASES, ids=[case[0] for case in _CASES])
def test_extract_code_blocks(name: str, content: str, wanted: list[str]) -> None:
    assert ExtractCodeBlocks(content) == wanted, name

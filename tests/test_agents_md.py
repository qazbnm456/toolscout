"""The agent guide is AGENTS.md alone: no CLAUDE.md may shadow it, it fits Codex's budget, its links resolve."""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKIP = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "output"}


def test_no_claude_md_anywhere():
    """Claude Code reads AGENTS.md only when a directory has no CLAUDE.md; one would hide the guide."""
    found = []
    for dirpath, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        found += [os.path.relpath(os.path.join(dirpath, f), ROOT) for f in files if f == "CLAUDE.md"]
    assert found == []


def test_agents_md_fits_codex_and_links_resolve():
    path = os.path.join(ROOT, "AGENTS.md")
    assert os.path.getsize(path) < 32 * 1024   # Codex's project_doc_max_bytes, root + nested guides
    text = open(path, encoding="utf-8").read()
    for target in re.findall(r"\]\(([^)#]+)\)", text):
        if not target.startswith("http"):
            assert os.path.exists(os.path.join(ROOT, target)), target


def test_docs_it_points_at_exist_and_handoff_names_no_claude_md():
    for doc in ("docs/INVARIANTS.md", "docs/HANDOFF.md"):
        text = open(os.path.join(ROOT, doc), encoding="utf-8").read()
        assert "CLAUDE.md" not in text, doc

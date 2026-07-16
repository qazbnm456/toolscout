"""The one-way dependency fence: toolscout NEVER imports toolscout_eval; the reverse is the design.

Mirrors the subprocess + sys.modules pattern of toolscout's tests/test_public_api.py — a fresh
interpreter, so no previously-imported module can mask a violation.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys


def test_import_toolscout_does_not_import_toolscout_eval():
    """The rollout core must stay eval-free: importing toolscout may not pull the harness. If this ever
    fails, an eval score has a path back into the rollout — the exact violation the fence prevents."""
    code = "import sys, toolscout; assert 'toolscout_eval' not in sys.modules; print('ok')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_import_toolscout_eval_pulls_toolscout_one_way_and_stays_light():
    """The harness reads toolscout's contract (the one-way direction) without dragging dspy or openai
    at import time — scoring with the stub judge needs neither."""
    code = ("import sys, toolscout_eval; assert 'toolscout' in sys.modules; "
            "assert 'dspy' not in sys.modules; assert 'openai' not in sys.modules; print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_toolscout_source_never_references_the_harness():
    """Belt and braces: no module in the toolscout package may even NAME toolscout_eval."""
    import toolscout

    package_dir = pathlib.Path(toolscout.__file__).resolve().parent
    offenders = [str(p) for p in sorted(package_dir.rglob("*.py"))
                 if "toolscout_eval" in p.read_text(encoding="utf-8")]
    assert offenders == []

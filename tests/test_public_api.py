"""The public surface — `import toolscout` is dspy-free; __all__ resolves; version is in sync."""

from __future__ import annotations

import subprocess
import sys


def test_import_toolscout_is_dspy_free():
    """`import toolscout` must NOT pull dspy (the lazy-reexport invariant). Checked in a fresh process."""
    code = "import sys, toolscout; assert 'dspy' not in sys.modules; print('ok')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_all_names_resolve():
    import toolscout

    for name in toolscout.__all__:
        assert getattr(toolscout, name) is not None


def test_version_matches_pyproject():
    import pathlib
    import tomllib

    import toolscout

    root = pathlib.Path(toolscout.__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert data["project"]["version"] == toolscout.__version__


def test_lazy_dspy_bearing_names_are_deferred():
    """The dspy-bearing names live behind __getattr__; they resolve, but only import dspy on access."""
    import toolscout

    for name in ("SolveTask", "setup", "run", "solve_task", "make_rubric_judge_tool"):
        assert callable(getattr(toolscout, name))

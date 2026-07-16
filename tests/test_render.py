"""render_outcome / render_response — human text; fabrication tells are surfaced prominently."""

from __future__ import annotations

from toolscout.assemble import outcome_from_events
from toolscout.render import render_outcome, render_response
from toolscout.response import build_response

from tests.conftest import run_recorded


def _events(tmp_path, outcome):
    return run_recorded(tmp_path, [
        ("load_server", {"server": "math"}),
        ("call_tool", {"server": "math", "tool": "add", "args": {"a": 6, "b": 7}}),
    ], outcome=outcome)


def test_render_outcome_shows_answer_and_toolspace_use(tmp_path):
    events = _events(tmp_path, {"answer": "13", "summary": "used math.add"})
    text = render_outcome(outcome_from_events(events))
    assert "ANSWER:" in text and "13" in text
    assert "TOOLSPACE USE" in text and "math:add" in text
    assert "RUBRIC CRITERIA" in text


def test_render_outcome_surfaces_fabrication_tells(tmp_path):
    events = _events(tmp_path, {"answer": "13", "servers_loaded": ["ghost"],
                                "tools_used": ["phantom_tool"]})
    text = render_outcome(outcome_from_events(events))
    assert "FABRICATION TELLS" in text
    assert "ghost" in text and "phantom_tool" in text


def test_render_response_header(tmp_path):
    events = _events(tmp_path, {"answer": "13"})
    resp = build_response(outcome_from_events(events), events, "run-1")
    text = render_response(resp)
    assert text.startswith("[ok] run=run-1")
    assert "ANSWER:" in text

"""Eval tasksets — the fuzzy task the planner sees + the concrete reference only the judge sees.

That split is ATLAS's fuzzy-vs-concrete design: `task` goes to `toolscout solve`; `reference` is the
enumerated expected-behavior description consumed ONLY by the eval judge. It is non-machine-checkable
by design — these tasks admit multiple valid trajectories, so there is no gold answer and no hard
expected-tool label; the judgement is deferred to the LLM judge exactly as the paper does.

`demo_taskset()` is the built-in CI-grade set over toolscout's offline demo catalog servers
(echo / math / memory / text): deterministic, hang-proof, zero creds. Pure stdlib + pydantic.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field


class EvalTask(BaseModel):
    """One eval task: id (pairs a run to its task via the run_id == task id convention), the fuzzy
    task text, and the judge-only reference."""

    id: str
    task: str = Field(..., description="the FUZZY task the planner sees (goes to toolscout solve)")
    reference: str = Field("", description="concrete expected behavior, JUDGE-ONLY (never shown to the planner)")


def load_taskset(path: str) -> list[EvalTask]:
    """Load a taskset JSON: a list of {id, task, reference?} objects (or {"tasks": [...]})."""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, dict):
        raw = raw.get("tasks")
    if not isinstance(raw, list):
        raise ValueError("taskset JSON must be a list of {id, task, reference?} objects")
    tasks: list[EvalTask] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("id") or not item.get("task"):
            raise ValueError(f"taskset item {i} must be an object with non-empty 'id' and 'task' fields")
        tasks.append(EvalTask(id=str(item["id"]), task=str(item["task"]),
                              reference=str(item.get("reference", ""))))
    ids = [t.id for t in tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate task ids in taskset: {dupes}")
    return tasks


def demo_taskset() -> list[EvalTask]:
    """The built-in offline set over the demo catalog's echo/math/memory/text servers — the CI fixture
    that proves the pipeline end-to-end with the stub judge and zero creds."""
    return [
        EvalTask(
            id="demo-echo",
            task="Echo the text 'hello' back to me.",
            reference="(1) load the echo server; (2) call echo(text='hello') -> 'hello'; "
                      "(3) answer with the echoed text 'hello'.",
        ),
        EvalTask(
            id="demo-math",
            task="What is 6 times 7?",
            reference="(1) load the math server; (2) call mul(a=6, b=7) -> 42; (3) answer 42, "
                      "grounded in the tool's returned value.",
        ),
        EvalTask(
            id="demo-memory",
            task="Store the value 'blue' under the key 'color', then read it back.",
            reference="(1) load the memory server; (2) call set(key='color', value='blue'); "
                      "(3) call get(key='color') -> 'blue'; (4) answer 'blue' from the get result.",
        ),
        EvalTask(
            id="demo-text",
            task="Upper-case the word 'ok' and count the words in 'a b c'.",
            reference="(1) load the text server; (2) call upper(text='ok') -> 'OK'; "
                      "(3) call wordcount(text='a b c') -> 3; (4) answer both 'OK' and 3.",
        ),
    ]

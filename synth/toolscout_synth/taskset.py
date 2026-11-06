"""Generate and load the shippable artifact: `<dir>/snapshots/*.json` + `taskset.jsonl` + `MANIFEST.json`.

`generate_all(out_dir)` writes the world snapshots, samples task instances per template × level, computes
each gold answer through the catalog, and records the hashes that pin it all. The directory is
self-contained: tar it, ship it, and point `build_catalog(data_dir)` at it.

Gold answers live ONLY here (in `taskset.jsonl`). The runner never passes them to toolscout, so they never
reach a trace's `run_start` meta: join gold to a trace by task id, outside the trace.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from typing import Any

from .catalog import TABLES, catalog_from_world
from .templates import HELDOUT_TEMPLATES, TEMPLATES
from .world import DEFAULT_SEED, EXT_VERSION, GENERATOR_VERSION, dumps, generate_full

DEFAULT_PER_LEVEL = 10
_MAX_ATTEMPTS = 200


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot_digest(file_shas: dict[str, str]) -> str:
    """One digest over every snapshot file, carried on each task so a task and its data can't drift apart."""
    return _sha("".join(f"{k}:{file_shas[k]}\n" for k in sorted(file_shas)))


def build_tasks(world: dict[str, Any], *, seed: int = DEFAULT_SEED, per_level: int = DEFAULT_PER_LEVEL,
                digest: str = "") -> list[dict]:
    """Sample `per_level` instances per template × level; gold comes from the template's tool-path solver."""
    catalog = catalog_from_world(world)
    touched: set[str] = set()

    def call(server, tool, args):
        touched.add(f"{server}:{tool}")
        return catalog.call(server, tool, args)

    tasks: list[dict] = []
    for tid, t in TEMPLATES.items():
        for level in t.levels:
            seen: set[str] = set()
            for i in range(t.per_level or per_level):
                for attempt in range(_MAX_ATTEMPTS):
                    rng = random.Random(f"{seed}:{tid}:{level}:{i}:{attempt}")
                    params = t.sample(rng, world, level)
                    text = t.render(params)
                    if text in seen:
                        continue
                    touched.clear()
                    gold = t.solve(call, params)
                    if gold is not None and t.accept(gold):
                        break
                else:
                    raise RuntimeError(f"{tid} level {level} #{i}: no acceptable instance in "
                                       f"{_MAX_ATTEMPTS} attempts")
                seen.add(text)
                static = t.servers is not None and (t.static_levels is None or level in t.static_levels)
                tools = list(t.tools) if static else sorted(touched)
                servers = list(t.servers) if static else sorted({x.split(":")[0] for x in touched})
                record = {
                    "id": f"{tid}-L{level}-{i:03d}", "template": tid, "split": t.split, "level": level,
                    "task": text, "gold_kind": t.gold_kind, "gold": gold, "tolerance": t.tolerance,
                    "servers_required": servers, "tools_required": tools,
                    "params": params, "snapshot_sha256": digest, "generator_version": GENERATOR_VERSION,
                }
                if t.choices is not None:
                    record["choices"] = t.choices(world)
                tasks.append(record)
    return tasks


def generate_all(out_dir: str, *, seed: int = DEFAULT_SEED, per_level: int = DEFAULT_PER_LEVEL) -> dict:
    """Write snapshots + taskset.jsonl + MANIFEST.json under `out_dir`; return the manifest."""
    world = generate_full(seed)
    snap_dir = os.path.join(out_dir, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    file_shas = {}
    for t in TABLES:
        text = dumps(world[t])
        with open(os.path.join(snap_dir, f"{t}.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        file_shas[f"snapshots/{t}.json"] = _sha(text)
    digest = snapshot_digest(file_shas)
    # Round-trip through JSON so gold is computed on exactly what build_catalog will load.
    world = {t: json.loads(dumps(world[t])) for t in TABLES}
    tasks = build_tasks(world, seed=seed, per_level=per_level, digest=digest)
    taskset = "".join(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n" for r in tasks)
    with open(os.path.join(out_dir, "taskset.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(taskset)
    counts: dict[str, int] = {}
    for r in tasks:
        counts[r["template"]] = counts.get(r["template"], 0) + 1
    manifest = {
        "generator_version": GENERATOR_VERSION, "ext_version": EXT_VERSION, "seed": seed, "per_level": per_level,
        "snapshot_sha256": digest, "files": file_shas, "taskset_sha256": _sha(taskset),
        "n_tasks": len(tasks), "tasks_per_template": counts,
        "heldout_templates": HELDOUT_TEMPLATES,
        "templates": {tid: {"split": t.split, "gold_kind": t.gold_kind, "levels": list(t.levels),
                            "servers": sorted({x for r in tasks if r["template"] == tid for x in r["servers_required"]})}
                      for tid, t in TEMPLATES.items()},
    }
    with open(os.path.join(out_dir, "MANIFEST.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def load_taskset(data_dir: str | None = None) -> list[dict]:
    from .catalog import default_data_dir

    with open(os.path.join(data_dir or default_data_dir(), "taskset.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]

"""`toolscout-synth gen [DIR]` regenerates the data dir; `toolscout-synth verify [DIR]` proves it is intact."""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import sys
import tempfile

from .catalog import default_data_dir
from .taskset import DEFAULT_PER_LEVEL, generate_all
from .world import DEFAULT_SEED


def _gen(args) -> int:
    m = generate_all(args.dir, seed=args.seed, per_level=args.per_level)
    print(f"wrote {m['n_tasks']} tasks over {len(m['templates'])} templates to {args.dir} "
          f"(held out: {', '.join(m['heldout_templates'])})")
    return 0


def _verify(args) -> int:
    """Regenerate from the recorded seed into a temp dir and compare every file byte-for-byte."""
    with open(os.path.join(args.dir, "MANIFEST.json"), encoding="utf-8") as fh:
        m = json.load(fh)
    with tempfile.TemporaryDirectory() as tmp:
        generate_all(tmp, seed=m["seed"], per_level=m["per_level"])
        names = [*m["files"], "taskset.jsonl", "MANIFEST.json"]
        bad = [n for n in names if not filecmp.cmp(os.path.join(tmp, n), os.path.join(args.dir, n),
                                                  shallow=False)]
    if bad:
        print(f"DRIFT: {bad} differ from a fresh regeneration (seed {m['seed']})", file=sys.stderr)
        return 1
    print(f"ok: {m['n_tasks']} tasks, snapshot {m['snapshot_sha256'][:12]}, regenerates byte-for-byte")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="toolscout-synth", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="write snapshots + taskset.jsonl + MANIFEST.json")
    g.add_argument("dir", nargs="?", default=default_data_dir())
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.add_argument("--per-level", type=int, default=DEFAULT_PER_LEVEL)
    g.set_defaults(func=_gen)
    v = sub.add_parser("verify", help="regenerate and compare byte-for-byte")
    v.add_argument("dir", nargs="?", default=default_data_dir())
    v.set_defaults(func=_verify)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

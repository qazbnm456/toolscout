"""toolscout-synth — a deterministic, offline, gold-answer taskset over a fictional multi-server toolspace.

`build_catalog()` is the toolspace (a toolscout `StaticCatalog`), `load_taskset()` the tasks with gold
answers, `check()` the pure answer check, and `run_task()` one toolscout run + check. Reward-free: `check`
reports a fact; using it for training is the downstream trainer's decision.
"""

from .catalog import DISTRACTOR_SERVERS, build_catalog, default_data_dir, load_world
from .check import CheckResult, check, check_outcome, render_gold
from .runner import SynthRun, run_task
from .taskset import generate_all, load_taskset
from .templates import HELDOUT_TEMPLATES, TEMPLATES

__version__ = "0.1.0"

__all__ = ["DISTRACTOR_SERVERS", "HELDOUT_TEMPLATES", "TEMPLATES", "CheckResult", "SynthRun", "build_catalog",
           "check", "check_outcome", "default_data_dir", "generate_all", "load_taskset", "load_world", "render_gold",
           "run_task", "__version__"]

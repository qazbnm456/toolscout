"""toolscout-studio — the visualization console for toolscout runs (workspace member, not in the wheel).

Serves a run's `TaskResponse` and replays its JSONL trace as live Server-Sent Events, rendering the
ISL/ITL/PTC trajectory (servers loaded, tools described/called, specialist escalations), the grounded
answer, and the rubric criteria facts. Reads toolscout's trace/response CONTRACT only — it never forks the
harness. The web stack (fastapi/uvicorn) + the `live` extra (which pulls toolscout) stay behind the
package boundary, so a harness-only install of toolscout never drags in the studio.
"""

from __future__ import annotations

__version__ = "0.1.0"

/* Pure, DOM-free decisions for the live-run driver — require-able + unit-tested (tests/run-core.test.js),
   loaded as a plain <script> before app.js (which reads the RunCore global, same as ReplayCore). Kept out
   of app.js so two invariants have a test seam the DOM IIFE cannot give:
   1. planTerminal — EVERY live-run outcome finalizes the stage (the skeleton-hang guard: a 409/"kept"
      branch that set state but rendered nothing left the "Solving…" skeleton spinning forever).
   2. deriveState — the card frame is keyed to the TRACE-DERIVED grounding, NOT the planner's self-report.
      This is toolscout's load-bearing signature (schema.py: heavy facts are re-sourced from the trace,
      and a claim the trace does not back is a fabrication tell) — keying the frame to the trace-derived
      truth, not the policy's self-report. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.RunCore = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // Classify one live-run attempt's terminal outcome → { status, stage, wroteResponse }.
  //   err         — null on a clean stream end (a `task.run.completed` event already carried the
  //                 response); `{status: 409}` when a finalized run already owns this id; else any thrown
  //                 error (network drop / HTTP 5xx).
  //   finalStatus — the status carried by the completed event (used only on the clean-end path).
  // `stage` is NEVER null — that is exactly the invariant the skeleton-hang bug violated:
  //   "card"      the completed event already produced the result; the caller re-GETs + renders the card
  //   "existing"  this id already had a finalized run — the 409 case; app.js prompts overwrite-or-keep
  //   "failed"    render a failed stage card (clears the "Solving…" skeleton)
  // `wroteResponse` — a durable response now exists for this id (a completion, or a pre-existing 409).
  function planTerminal(err, finalStatus) {
    if (!err) return { status: finalStatus, stage: "card", wroteResponse: true };
    if (err.status === 409) return { status: "kept", stage: "existing", wroteResponse: true };
    return { status: "failed", stage: "failed", wroteResponse: false };
  }

  // Derive the card's alloy STATE from a TaskResponse — keyed to the trace-re-sourced facts, not the
  // planner's self-report → { key, head, tells }:
  //   "iron"      — status failed/refused, or no usable answer (a REFUSAL card; the effort still shows)
  //   "flag"      — an answer, but the self-report OVER-CLAIMS: it cites criteria / servers / tools the
  //                 trace does not back (cited_unknown ∪ unbacked_servers ∪ unbacked_tools). Amber; the
  //                 console spells out that the trace does not back the claim. This is the money shot.
  //   "grounded"  — an answer whose self-report matches the trace (no fabrication tell). Green.
  function deriveState(r) {
    const status = (r && r.status) || "";
    const outcome = (r && r.outcome) || null;
    const answer = outcome && String(outcome.answer || "").trim();
    if (status === "failed" || status === "refused" || !answer)
      return { key: "iron", head: status === "refused" ? "REFUSED" : "FAILED", tells: 0 };
    const tells = (outcome.cited_unknown || []).length +
                  (outcome.unbacked_servers || []).length +
                  (outcome.unbacked_tools || []).length;
    if (tells > 0) return { key: "flag", head: "FLAGGED", tells: tells };
    return { key: "grounded", head: "GROUNDED", tells: 0 };
  }

  return { planTerminal: planTerminal, deriveState: deriveState };
});

---
name: verify-before-finalize
description: The pre-SUBMIT checklist — did you answer every part of the task, are all claims grounded, are your cited server/tool lists honest, and (if enabled) what did rubric_judge observe?
---

# Verify before finalize

Run this checklist in the REPL right before you submit the `TaskOutcome`. It's cheap and it catches the
two failure modes that ship a bad run: an INCOMPLETE answer and an UNGROUNDED or padded one.

## The checklist
1. **Completeness — every part answered.** Re-read the original task and enumerate its parts. "what is
   6 * 7, then uppercase 'ok'?" has TWO asks; your answer must contain both (42 AND "OK"). A half answer
   to a two-part task is a fail.
2. **Grounding — every claim rests on a held value.** For each statement in `answer`/`summary`, point to
   the variable that backs it (see `ground-the-answer`). If you can't, either fetch the value or drop the
   claim.
3. **Honest citations.** `servers_loaded` = only servers you loaded. `tools_used` = only tools you
   called successfully. `cited_criteria` = only real criteria. No padding — the trace flags
   `unbacked_servers`/`unbacked_tools`/`cited_unknown`.
4. **Budget.** If you're near the iteration cap, prefer submitting a grounded partial answer over one
   more exploratory call. Shipping something honest beats never submitting.

## The optional `rubric_judge` self-check
If the deployment enables the `rubric_judge(draft)` tool, call it ONCE here on your draft outcome and set
`judge_call_id` to the returned id. It emits per-criterion OBSERVATIONS (a note + met/unmet for Task
Fulfillment / Tool Appropriateness / Tool Grounding / Parameter Accuracy) — a check, NOT a score.
- Weigh each observation: an "unmet" on Task Fulfillment means you missed a part → go fix it, then
  re-verify. An "unmet" on Tool Grounding means a claim isn't backed → ground it or cut it.
- Call it once, not in a loop. It's an expensive self-check, and its output is advisory input to YOUR
  judgement — you decide, then submit. Don't treat "all met" as permission to skip the checklist above.
- If the tool isn't enabled, do steps 1–4 by hand; they are the same criteria.

## Then SUBMIT
When completeness, grounding, and honest citations all hold, submit the `TaskOutcome`. Don't keep
exploring for polish — a verified answer is done.

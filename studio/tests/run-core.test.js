/* Unit tests for the pure run-driver core (run: `node tests/run-core.test.js`). Node 16 lacks the
   built-in test runner, so this is a tiny assert-based harness that exits non-zero on any failure. */
"use strict";
const assert = require("assert");
const RC = require("../static/run-core.js");

let failed = 0;
function test(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failed++; console.error("  FAIL " + name + "\n       " + e.message); }
}

// ---- planTerminal: every terminal outcome maps to a stage-finalizing action (the skeleton-hang guard) ----
test("clean end → 'card' (the completed event already drew the stage), response written", () => {
  assert.deepStrictEqual(RC.planTerminal(null, "ok"),
    { status: "ok", stage: "card", wroteResponse: true });
});
test("409 → show the EXISTING stored run as 'kept'; a response exists → mark wroteResponse", () => {
  assert.deepStrictEqual(RC.planTerminal({ status: 409 }, "failed"),
    { status: "kept", stage: "existing", wroteResponse: true });
});
test("other error (network / 5xx) → clear the skeleton via a failed stage; no response written", () => {
  assert.deepStrictEqual(RC.planTerminal(new Error("boom"), "failed"),
    { status: "failed", stage: "failed", wroteResponse: false });
  assert.deepStrictEqual(RC.planTerminal({ status: 500 }, "failed"),
    { status: "failed", stage: "failed", wroteResponse: false });
});
test("INVARIANT: EVERY outcome finalizes the stage (the bug was a branch that finalized nothing)", () => {
  const outcomes = [null, { status: 409 }, { status: 500 }, { status: 503 },
                    new Error("x"), { status: 409, message: "y" }, {}];
  for (const err of outcomes) {
    const p = RC.planTerminal(err, "ok");
    assert.ok(["card", "existing", "failed"].includes(p.stage),
      "stage must be a finalizing action for outcome " + JSON.stringify(err));
  }
});

// ---- deriveState: the frame is keyed to the TRACE-derived grounding, not the planner's self-report ----
test("an answer with no fabrication tell → GROUNDED (green)", () => {
  const st = RC.deriveState({ status: "ok", outcome: { answer: "42", servers_loaded: ["math"] } });
  assert.deepStrictEqual(st, { key: "grounded", head: "GROUNDED", tells: 0 });
});
test("an answer that over-claims (unbacked servers/tools) → FLAGGED (amber)", () => {
  const s1 = RC.deriveState({ status: "ok", outcome: { answer: "42", unbacked_servers: ["ghost"] } });
  assert.strictEqual(s1.key, "flag"); assert.strictEqual(s1.head, "FLAGGED"); assert.strictEqual(s1.tells, 1);
  const s2 = RC.deriveState({ status: "ok", outcome: { answer: "42", unbacked_servers: ["g"], unbacked_tools: ["t"] } });
  assert.strictEqual(s2.key, "flag"); assert.strictEqual(s2.tells, 2);
});
test("failed / refused / answerless → iron REFUSAL card, tells suppressed", () => {
  assert.strictEqual(RC.deriveState({ status: "failed", outcome: null }).key, "iron");
  assert.strictEqual(RC.deriveState({ status: "failed", outcome: null }).head, "FAILED");
  assert.strictEqual(RC.deriveState({ status: "refused", outcome: { answer: "" } }).head, "REFUSED");
  // status ok but the answer is blank → still iron (no usable answer)
  assert.strictEqual(RC.deriveState({ status: "ok", outcome: { answer: "   " } }).key, "iron");
});
test("INVARIANT: deriveState always yields one of the three alloys, tells never negative", () => {
  const cases = [{}, { status: "ok" }, { status: "ok", outcome: {} },
                 { status: "ok", outcome: { answer: "a" } }, { status: "weird", outcome: { answer: "a", unbacked_tools: ["x"] } }];
  for (const r of cases) {
    const st = RC.deriveState(r);
    assert.ok(["grounded", "flag", "iron"].includes(st.key), "alloy for " + JSON.stringify(r));
    assert.ok(st.tells >= 0);
  }
});

// ---- slugId / deriveRunId: PARITY mirrors of app.py `_slug_id` / `_derive_run_id` (the preview the
// console shows must be the id the server files the run under — cases mirror tests/test_app.py) ----
test("deriveRunId slugs the task's leading words (parity: 'what is 6 * 7?' → what-is-6-7)", () => {
  assert.strictEqual(RC.deriveRunId("what is 6 * 7?"), "what-is-6-7");
  assert.strictEqual(RC.deriveRunId("Store the number 42 under the key 'answer'"), "Store-the-number-42-under-the");
});
test("slugId sanitizes an explicit id (parity: 'My Run/01' → My-Run-01), never a traversal segment", () => {
  assert.strictEqual(RC.slugId("My Run/01"), "My-Run-01");
  assert.strictEqual(RC.slugId("../../etc/passwd"), "etc-passwd");
  assert.strictEqual(RC.slugId("???"), "unknown");
});
test("deriveRunId never yields an empty id (empty task → 'task'; all-folded words → 'unknown')", () => {
  assert.strictEqual(RC.deriveRunId(""), "task");
  assert.strictEqual(RC.deriveRunId("   "), "task");
  assert.strictEqual(RC.deriveRunId("計算 6 乘 7"), "6-7");   // non-ASCII folds; digits survive
  assert.strictEqual(RC.deriveRunId("計算"), "unknown");      // every char folds → the slug fallback
});
test("slugging is idempotent (the server re-slugs what the console sends — same id out)", () => {
  for (const raw of ["what is 6 * 7?", "My Run/01", "..a..", "計算 6 乘 7", "", "x".repeat(500)]) {
    const once = RC.slugId(raw);
    assert.strictEqual(RC.slugId(once), once, "not idempotent for " + JSON.stringify(raw));
  }
});
test("slugId length-caps at 120 (parity: app.py _RUN_ID_MAX), never leaving a trailing '-'/'.'", () => {
  assert.strictEqual(RC.slugId("x".repeat(500)).length, 120);
  const edge = RC.slugId("a".repeat(119) + "-tail");   // the cut lands on the '-'
  assert.ok(edge.length <= 120 && !/[-.]$/.test(edge), "edge slug is " + JSON.stringify(edge));
});

console.log(failed ? "\n" + failed + " test(s) FAILED" : "\nall passing");
process.exit(failed ? 1 : 0);

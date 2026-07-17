/* toolscout-studio frontend — the toolspace console. Zero-build vanilla JS. Reads window.ReplayCore,
   window.RunCore, window.Trajectory (loaded before this). See DESIGN.md for the visual contract. The
   load-bearing rule (§2): the card frame is keyed to the TRACE-DERIVED grounding (RunCore.deriveState —
   facts re-sourced from the trace, a claim the trace does not back is a fabrication tell), NOT the
   planner's self-report — key the card frame to the evidence, never the claim. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmtBytes = (n) => n == null ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  const formatElapsed = (s) => { if (s == null) return ""; s = Math.round(s); const m = Math.floor(s / 60), r = s % 60; return m ? `${m}m ${String(r).padStart(2, "0")}s` : `${r}s`; };
  const tint = (s) => esc(s);   // toolscout REPL code is Python/JSON — escape only (no highlighter)
  const _linkify = (s) => /^https?:\/\//.test(s) ? `<a href="${esc(s)}" target="_blank" rel="noopener">${esc(s)} ↗</a>` : esc(s);

  const ICONS = {
    list: '<svg viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>',
    load: '<svg viewBox="0 0 24 24"><path d="M12 3v9M8 8l4 4 4-4"/><rect x="4" y="15" width="16" height="5" rx="1"/></svg>',
    describe: '<svg viewBox="0 0 24 24"><path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M10 12h5M10 16h5"/></svg>',
    call: '<svg viewBox="0 0 24 24"><path d="M13 3L5 13h6l-1 8 8-11h-6z"/></svg>',
    judge: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg>',
    specialist: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.5"/><path d="M12 3v5M12 16v5M3 12h5M16 12h5"/></svg>',
    skill: '<svg viewBox="0 0 24 24"><path d="M5 4h9a2 2 0 012 2v14H7a2 2 0 01-2-2z"/><path d="M9 4v12"/></svg>',
    flag: '<svg viewBox="0 0 24 24"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
    scope: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4M1 12h4M19 12h4"/></svg>',
  };

  const feedEl = $("#feed"), stageEl = $("#stage-col"), metaEl = $("#meta-col"), layoutEl = $(".layout");
  let CONFIG = { models: {}, max_iterations: 30, toolspace: "demo" };
  let currentRunId = null, busy = false;
  const getRunId = () => currentRunId, isBusy = () => busy;

  const traj = window.Trajectory({ $, esc, feedError, ICONS, fmtBytes, tint, formatElapsed, _linkify, getRunId, isBusy });

  function setBusy(b) { busy = b; $("#solve").disabled = b || !validInput(); traj.refreshTransport(); }
  function feedError(e) { addFeedRow("task.run.completed", { _error: String(e && e.message || e) }); }

  // ── SSE ───────────────────────────────────────────────────────────────
  async function streamSSE(method, url, body, onEvent) {
    const resp = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    if (!resp.ok) { const err = new Error(`HTTP ${resp.status}`); err.status = resp.status; try { err.detail = (await resp.json()).detail; } catch (_) {} throw err; }
    const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const em = /^event: (.*)$/m.exec(chunk), dm = /^data: (.*)$/m.exec(chunk);
        if (em) { let data = {}; try { data = dm ? JSON.parse(dm[1]) : {}; } catch (_) {} onEvent(em[1].trim(), data); }
      }
    }
  }

  // ── live feed rows ──────────────────────────────────────────────────────
  // The feed shows ACTIONS only, in BOTH live and replay. `task.plan.step` (planner reasoning) and
  // `task.result.done` are deliberately absent — reasoning lives in the Trajectory drawer, and an
  // unmapped event falls through `FEED[event] || (() => null)` to no row (never a stuck skeleton).
  const FEED = {
    "task.servers.listed": (d) => ({ icon: "list", fam: "fam-signal", label: "List servers", meta: `${d.n || 0} server${d.n === 1 ? "" : "s"}` }),
    "task.server.loaded": (d) => ({ icon: "load", fam: d.ok ? "fam-load" : "fam-bad", label: d.ok ? "Load server" : "Load server · failed",
      meta: d.server, detail: d.ok && (d.tools || []).length ? `tools: ${(d.tools || []).join(", ")}` : "" }),
    "task.tools.described": (d) => ({ icon: "describe", fam: "fam-itl", label: "Describe tools", meta: `${d.n || 0}`, detail: (d.described || []).join(", ") }),
    "task.tool.called": (d) => ({ icon: "call", fam: d.ok ? "fam-ok" : "fam-bad",
      label: d.ok ? "Call tool" : `Call tool · ${d.reason || "failed"}`, meta: `${d.server || ""}:${d.tool || ""}`, detail: d.ok ? "" : d.error }),
    "task.judge": (d) => ({ icon: "judge", fam: d.circuit_broken || d.error ? "fam-bad" : "fam-judge",
      label: d.circuit_broken ? "Rubric judge · circuit broke" : d.error ? "Rubric judge · error" : "Rubric judge", meta: d.n != null ? `${d.n} obs` : "" }),
    "task.skill.read": (d) => ({ icon: "skill", fam: "fam-skill", label: "Read skill", meta: d.name }),
    "task.specialist.escalation": (d) => ({ icon: "specialist", fam: "fam-specialist", label: "Ask specialist", detail: d.question, reason: true }),
    "task.run.created": () => ({ icon: "flag", fam: "fam-signal", label: "Solve started" }),
    "task.run.completed": (d) => ({ icon: "flag", fam: d._error ? "fam-bad" : "fam-signal", label: d._error ? "Stream error" : "Finalized", detail: d._error }),
  };
  function addFeedRow(event, data) {
    const spec = (FEED[event] || (() => null))(data); if (!spec) return;
    const atBottom = feedEl.scrollHeight - feedEl.scrollTop - feedEl.clientHeight < 40;
    const row = document.createElement("div"); row.className = "feed-row enter";
    row.innerHTML = `<div class="fr-ic ${spec.fam}">${ICONS[spec.icon] || ""}</div><div class="fr-body">` +
      `<div class="fr-line"><span class="fr-primary">${esc(spec.label)}</span>${spec.meta ? `<span class="fr-meta">${esc(spec.meta)}</span>` : ""}</div>` +
      `${spec.detail ? `<div class="fr-detail${spec.reason ? " reason" : ""}">${esc(spec.detail)}</div>` : ""}</div>`;
    feedEl.appendChild(row);
    if (atBottom) feedEl.scrollTop = feedEl.scrollHeight;
  }

  // ── render the result ───────────────────────────────────────────────────
  let CURRENT = null, stageView = "answer";
  function renderResult(r) {
    CURRENT = r; stageView = "answer";
    currentRunId = r.id || currentRunId;
    renderStage(r); renderModules(r);
    layoutEl.classList.remove("no-meta");
    traj.showHandle();
  }

  function renderStage(r) {
    const st = RunCore.deriveState(r);
    const isRefusal = st.key === "iron";
    // ONE page-height alloy card (frame = derived grounding, §2), two views behind the top-right switch,
    // in read order: ANSWER → TOOLSPACE (the star: the ISL/ITL/PTC exploration + fabrication tells). The
    // original input is a right-column module ("Original input"), always visible — not a view here.
    const views = [["answer", "Answer"], ["toolspace", "Toolspace"]];
    if (!views.some(([v]) => v === stageView)) stageView = "answer";
    const switchHtml = `<div class="stage-switch">` + views.map(([v, label]) =>
      `<button data-view="${v}" class="${stageView === v ? "on" : ""}">${label}</button>`).join("") + `</div>`;
    let body;
    if (stageView === "toolspace") body = toolspaceHtml(r);
    else body = isRefusal ? refusalView(r) : answerView(r, st);
    stageEl.innerHTML = `<div class="card ${st.key} sweep"><div class="card-inner">` +
      `<div class="card-head"><span class="state-head ${st.key}">${esc(st.head)}</span>${switchHtml}</div>` +
      body + `</div></div>`;
    stageEl.querySelectorAll(".stage-switch button").forEach((b) => b.addEventListener("click", () => { stageView = b.dataset.view; renderStage(r); }));
  }

  function answerView(r, st) {
    const o = r.outcome || {}, p = r.process || {};
    const unbacked = [].concat(o.unbacked_servers || [], o.unbacked_tools || []);
    const marker = st.tells > 0
      ? `<span class="contradiction">⚠ the self-report is not fully backed by the trace — ` +
        (unbacked.length ? `claims ${unbacked.map(esc).join(", ")} with no recorded event; ` : "") +
        `the facts below are re-sourced from the trace</span>` : "";
    const badges = [
      `<span class="badge count">${p.servers_loaded != null ? p.servers_loaded : (o.servers_loaded || []).length} servers</span>`,
      `<span class="badge count">${p.tool_calls != null ? p.tool_calls : (o.tools_used || []).length} tool calls</span>`,
      `<span class="badge count">${p.turns != null ? p.turns : "—"} turns</span>`,
      (p.specialist_escalations ? `<span class="badge count">${p.specialist_escalations} escalations</span>` : ""),
      (p.judge_ran ? `<span class="badge judge">judge ✓</span>` : ""),
      (st.tells ? `<span class="badge fab">${st.tells} fabrication tell${st.tells === 1 ? "" : "s"}</span>` : ""),
    ].join("");
    return `<div class="card-head">${badges}</div>${marker}` +
      `<div class="answer-well">${esc(o.answer || "(no answer)")}</div>` +
      (o.summary ? `<p class="card-summary">${esc(o.summary)}</p>` : "");
  }

  function refusalView(r) {
    const rf = r.refusal || {}, p = r.process || {};
    const counters = `<div class="ini-budget">servers loaded ${p.servers_loaded || 0} · tool calls ${p.tool_calls || 0} · turns ${p.turns || 0}</div>`;
    return `<div class="refusal"><div class="rf-reason">${esc(rf.reason || r.status || "no answer")}</div>` +
      `<p>${esc(r.error || rf.reason || "The run did not produce a usable answer.")}</p></div>${counters}`;
  }

  // ── the toolspace view (the star: ISL → ITL → PTC) ───────────────────────
  function opRow(c) {
    const argStr = c.args != null && c.args !== "null" ? esc(c.args) : "";
    const inner = `<code>${esc(c.tool || "")}(${argStr})</code>` +
      (c.ok ? ` <span class="op-res">→ ${esc(c.result != null ? c.result : "ok")}</span>`
            : ` <span class="op-reason">✕ ${esc(c.reason || "failed")}</span>${c.error ? ` <span class="op-res">${esc(c.error)}</span>` : ""}`);
    return `<div class="op ${c.ok ? "ok" : "fail"}">${inner}</div>`;
  }
  function toolspaceHtml(r) {
    const o = r.outcome || {}, ops = r.toolspace_ops;
    const fab = [];
    if ((o.unbacked_servers || []).length) fab.push(`<div class="fab-well"><div class="fw-head">servers claimed, not in the trace</div><div class="fw-body">${(o.unbacked_servers || []).map(esc).join(", ")}</div></div>`);
    if ((o.unbacked_tools || []).length) fab.push(`<div class="fab-well"><div class="fw-head">tools claimed, not in the trace</div><div class="fw-body">${(o.unbacked_tools || []).map(esc).join(", ")}</div></div>`);
    if (!ops || !(ops.servers || []).length) {
      // No trace-derived ops (e.g. a replay with no trace, or a run that never touched the toolspace).
      // Fall back to the flat lists from the response outcome so the view is never blank.
      const flat = (o.servers_loaded || []).length || (o.tools_used || []).length
        ? `<div class="ts-group-label">servers loaded</div><div class="chips">${(o.servers_loaded || []).map((s) => `<span class="tchip">${esc(s)}</span>`).join("") || "—"}</div>` +
          `<div class="ts-group-label">tools used</div><div class="chips">${(o.tools_used || []).map((s) => `<span class="tchip">${esc(s)}</span>`).join("") || "—"}</div>`
        : `<div class="ts-empty">no toolspace operations recorded for this run.</div>`;
      return flat + fab.join("");
    }
    const listed = (ops.listed || []).length ? `<div class="ts-listed">index seen · ${(ops.listed || []).map(esc).join(", ")}</div>` : "";
    // loaded servers first (they carry the ISL/ITL/PTC story); then any touched-but-not-loaded server.
    const servers = [...ops.servers].sort((a, b) => (b.loaded ? 1 : 0) - (a.loaded ? 1 : 0));
    const blocks = servers.map((s) => {
      const tools = (s.tool_names || []).length ? `<span class="srv-tools">${(s.tool_names || []).map(esc).join(", ")}</span>` : "";
      const described = (s.described || []).length ? `<div class="srv-described">described (ITL): ${(s.described || []).map(esc).join(", ")}</div>` : "";
      const calls = (s.calls || []).map(opRow).join("");
      return `<div class="srv ${s.loaded ? "loaded" : ""}"><div class="srv-head"><span class="srv-name">${esc(s.name)}</span>` +
        `${s.loaded ? `<span class="srv-badge">loaded</span>` : ""}${tools}</div>${described}${calls}</div>`;
    }).join("");
    return listed + blocks + (fab.length ? `<div class="ts-group-label">fabrication tells</div>` + fab.join("") : "");
  }

  function renderModules(r) {
    const o = r.outcome || {}, p = r.process || {};
    const task = r.task || o.task || CURRENT_TASK;
    const stats = [
      ["turns", p.turns != null ? p.turns : "—", ""],
      ["servers", p.servers_loaded != null ? p.servers_loaded : "—", ""],
      ["tool calls", p.tool_calls != null ? p.tool_calls : "—", ""],
      ["escalations", p.specialist_escalations != null ? p.specialist_escalations : "—", p.specialist_escalations ? "specialist" : ""],
    ].map(([l, v, c]) => `<div class="stat ${c}"><div class="sv">${esc(v)}</div><div class="sl">${l}</div></div>`).join("");

    const crits = (o.criteria_facts || []).map((f) =>
      `<div class="crit"><div class="crit-top"><span class="crit-cat">${esc(f.category || "?")}</span>` +
      `<span class="crit-name">${esc(f.criterion || "")}${f.weight != null ? ` (w=${esc(f.weight)})` : ""}</span></div>` +
      `<div class="crit-obs">${esc(JSON.stringify(f.observed || {}))}</div></div>`).join("");
    const judge = (o.judge_observations || []).map((ob) => {
      const met = ob.met, cls = met == null ? "unk" : (met ? "met" : "unmet"), mark = met == null ? "?" : (met ? "met" : "UNMET");
      return `<div class="judge-obs"><span class="jm ${cls}">[${mark}]</span> <b>${esc(ob.criterion || "")}</b>: ${esc(ob.note || "")}</div>`;
    }).join("");

    const tells = [];
    if ((o.unbacked_servers || []).length) tells.push(["servers claimed, not in the trace", o.unbacked_servers]);
    if ((o.unbacked_tools || []).length) tells.push(["tools claimed, not in the trace", o.unbacked_tools]);
    const tellsHtml = tells.length
      ? tells.map(([label, list]) => `<div class="ind-group-label">${esc(label)}</div><div class="chips">${list.map((c) => `<span class="tchip fab">${esc(c)}</span>`).join("")}</div>`).join("")
      : `<div class="clean-note">✓ no fabrication tells — the self-report matches the trace</div>`;

    metaEl.innerHTML =
      module("Run telemetry", `<div class="headline">${p.turns != null ? esc(p.turns) : "—"} <span class="sl" style="font-size:.8rem">turns</span></div><div class="stat-grid">${stats}</div>` +
        (p.judge_ran ? `<span class="flag-chip">rubric judge ran</span>` : "")) +
      module("Original input", `<div class="task-well">${esc(task || "(no task recorded)")}</div>`) +
      module("Rubric criteria facts", (crits || `<div class="ts-empty">no rubric recorded for this run.</div>`) +
        (judge ? `<div class="ind-group-label">judge observations (labels — not a score)</div>${judge}` : "")) +
      module("Fabrication tells", tellsHtml) +
      (o.summary ? module("Summary", `<div class="prose">${esc(o.summary)}</div>`) : "");
  }
  function module(label, body) {
    return `<div class="module"><div class="module-cap"></div><div class="module-head">${esc(label)}</div><div class="module-body">${body}</div></div>`;
  }

  // ── skeleton / empty ────────────────────────────────────────────────────
  function showEmpty() {
    layoutEl.classList.add("no-meta");
    stageEl.innerHTML = `<div class="empty-stage"><span class="es-glyph">${ICONS.scope}</span>` +
      `Solve a task over the toolspace: type one, load an example, or open a past run.</div>`;
    metaEl.innerHTML = "";
  }
  function showSkeleton() {
    stageEl.innerHTML = `<div class="skeleton-card iron"><span class="sk-pulse"></span><div style="margin-top:14px">Solving…</div></div>`;
    metaEl.innerHTML = ""; layoutEl.classList.add("no-meta");
  }

  // ── solve (live) ────────────────────────────────────────────────────────
  function validInput() { return !!$("#task").value.trim(); }
  function refreshSolveBtn() { $("#solve").disabled = busy || !validInput(); }

  async function solve(overwrite) {
    const task = $("#task").value.trim();
    if (!task) return;
    CURRENT_TASK = task;
    feedEl.innerHTML = ""; showSkeleton(); traj.reset(); setBusy(true);
    let finalResp = null, finalStatus = null, err = null;
    try {
      await streamSSE("POST", "/v1/solve", { task, overwrite: !!overwrite }, (event, data) => {
        if (event === "task.run.created") { currentRunId = data.run_id || currentRunId; addFeedRow(event, data); }
        else if (event === "task.run.completed") { finalResp = data; finalStatus = data.status; addFeedRow(event, data); }
        else addFeedRow(event, data);
      });
    } catch (e) { err = e; }
    setBusy(false);
    const plan = RunCore.planTerminal(err, finalStatus);
    if (plan.stage === "existing") {   // 409 — a finalized run owns this id
      if (confirm(`A finalized run already exists for this task. Replace it?`)) return solve(true);
      showEmpty(); return;
    }
    if (plan.stage === "failed") { feedError(err); renderResult({ status: "failed", task: CURRENT_TASK, error: err && (err.detail || err.message), refusal: { reason: "stream_error" } }); return; }
    // clean end: the completed event carried the raw response; re-GET for the trace-derived
    // toolspace_ops augmentation (the ISL/ITL/PTC narrative the envelope omits — see /v1/runs/{id}).
    if (finalResp) {
      let full = finalResp;
      try { const g = await fetch(`/v1/runs/${encodeURIComponent(currentRunId)}`); if (g.ok) full = await g.json(); } catch (_) {}
      renderResult(full); refreshRuns();
    }
  }
  let CURRENT_TASK = null;

  // ── load a past run (replay) ────────────────────────────────────────────
  async function loadRun(id) {
    if (!id) return;
    currentRunId = id; feedEl.innerHTML = ""; showSkeleton(); traj.reset(); CURRENT_TASK = null;
    let resp = null;
    try {
      const r = await fetch(`/v1/runs/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(`no run ${id}`);
      resp = await r.json();
    } catch (e) { feedError(e); renderResult({ status: "failed", refusal: { reason: "not_found" }, error: String(e.message) }); return; }
    // dump the trace into the feed at once (no SSE pacing), then render the stored response. The
    // step-through "process build-up" lives in the Trajectory drawer's speed-aware replay, so pacing
    // the main feed too was redundant. (The backend `delay` knob stays, just unused by the UI.)
    try {
      await streamSSE("GET", `/v1/runs/${encodeURIComponent(id)}/events`, null, (event, data) => addFeedRow(event, data));
    } catch (_) {}
    renderResult(resp);
  }

  // ── config / examples / runs ────────────────────────────────────────────
  async function loadConfig() {
    try { CONFIG = await (await fetch("/v1/config")).json(); } catch (_) {}
    const m = CONFIG.models || {};
    [["planner", m.planner], ["specialist", m.specialist], ["judge", m.judge]].forEach(([role, name]) => {
      const chip = $(`#role-${role}`); if (!chip) return;
      chip.querySelector(".role-model").textContent = name || "";
      chip.title = name || role; chip.classList.toggle("ready", !!name);
    });
  }
  async function loadExamples() {
    let ex = [];
    try { ex = (await (await fetch("/v1/examples")).json()).examples || []; } catch (_) {}
    const sel = $("#example-pick");
    ex.forEach((e, i) => { const o = document.createElement("option"); o.value = String(i); o.textContent = `⚡ ${e.name}`; sel.appendChild(o); });
    sel._examples = ex;
  }
  async function refreshRuns() {
    try {
      const runs = (await (await fetch("/v1/runs")).json()).runs || [];
      $("#runs").innerHTML = runs.map((r) => `<option value="${esc(r)}"></option>`).join("");
      $("#runs-hint").textContent = runs.length ? `${runs.length} loadable run${runs.length === 1 ? "" : "s"}` : "no stored runs yet";
    } catch (_) {}
  }

  // ── theme ───────────────────────────────────────────────────────────────
  function initTheme() {
    const saved = localStorage.getItem("ts-theme");
    const dark = saved ? saved === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    $("#theme-toggle").textContent = dark ? "☾" : "☀";
  }
  $("#theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.getAttribute("data-theme") !== "dark";
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    localStorage.setItem("ts-theme", dark ? "dark" : "light");
    $("#theme-toggle").textContent = dark ? "☾" : "☀";
  });

  // ── wiring ──────────────────────────────────────────────────────────────
  $("#task").addEventListener("input", refreshSolveBtn);
  $("#solve").addEventListener("click", () => solve(false));
  $("#load").addEventListener("click", () => loadRun($("#load-id").value.trim()));
  $("#load-id").addEventListener("keydown", (e) => { if (e.key === "Enter") loadRun($("#load-id").value.trim()); });
  $("#example-pick").addEventListener("change", (e) => {
    const ex = e.target._examples || []; const f = ex[Number(e.target.value)];
    if (!f) { $("#example-note").hidden = true; return; }
    $("#task").value = f.task;
    $("#example-note").hidden = false;
    $("#example-note").innerHTML = `<b>${esc(f.name)}</b> — ${esc(f.note || "")}`;
    refreshSolveBtn();
  });

  initTheme(); loadConfig(); loadExamples(); refreshRuns(); showEmpty();
})();

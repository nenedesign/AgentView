// agentview dashboard - Milestone 1 Day 2
// Session list shell. Day 3 adds activity cards inside each session card.

const fixtureSelect = document.getElementById("fixture-select");
const sessionList   = document.getElementById("session-list");
const trustPanel    = document.getElementById("trust-panel");
const skippedBadge  = document.getElementById("skipped-badge");

// --- six-state session display map ---------------------------
const STATE = {
  completed:         { label: "Completed",         cls: "state--completed"         },
  exited_with_error: { label: "Ended with error",  cls: "state--exited_with_error" },
  interrupted:       { label: "Interrupted",       cls: "state--interrupted"       },
  active:            { label: "Active",             cls: "state--active"            },
  started:           { label: "Started",            cls: "state--started"           },
  unknown:           { label: "No shutdown recorded", cls: "state--unknown"        },
};

const ACT_CHIP = {
  completed:   { label: "Completed",                   cls: "act-chip--completed"   },
  empty:       { label: "Returned no usable data",     cls: "act-chip--empty"       },
  failed:      { label: "Failed",                      cls: "act-chip--failed"      },
  in_progress: { label: "In progress",                 cls: "act-chip--in_progress" },
};

// --- utilities -----------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "className") node.className = v;
    else if (k === "hidden") { if (v) node.hidden = true; }
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function formatTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
  } catch { return ""; }
}

function formatDuration(startedAt, endedAt) {
  if (!startedAt || !endedAt) return null;
  const ms = new Date(endedAt) - new Date(startedAt);
  if (isNaN(ms) || ms < 0) return null;
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

// --- session card builder ------------------------------------

function buildStatePill(state) {
  const conf = STATE[state] || { label: state, cls: "state--unknown" };
  return el("span", { className: `state-pill ${conf.cls}`, "aria-label": `Session state: ${conf.label}` },
    el("span", { className: "state-pill-dot", "aria-hidden": "true" }),
    conf.label,
  );
}

function buildActivitySummary(activities) {
  if (!activities || activities.length === 0) return null;

  const counts = {};
  for (const act of activities) {
    const s = act.status || "unknown";
    counts[s] = (counts[s] || 0) + 1;
  }

  const order = ["completed", "empty", "failed", "in_progress"];
  const chips = [];

  for (const status of order) {
    if (!counts[status]) continue;
    const conf = ACT_CHIP[status] || { label: status, cls: "" };
    chips.push(
      el("span", { className: `act-chip ${conf.cls}` },
        String(counts[status]), " ", conf.label,
      ),
    );
  }

  if (chips.length === 0) return null;

  return el("div", { className: "activity-summary", "aria-label": "Activity breakdown" }, ...chips);
}

function buildSessionCard(session) {
  const serverLabel = session.server_name || session.id || "Unknown server";
  const state       = session.state || "unknown";
  const acts        = session.activities || [];
  const duration    = formatDuration(session.started_at, session.ended_at);
  const timeStr     = formatTime(session.started_at);
  const dateStr     = formatDate(session.started_at);

  const metaItems = [];
  metaItems.push(el("span", {}, `${acts.length} tool call${acts.length !== 1 ? "s" : ""}`));
  if (duration) {
    metaItems.push(el("span", { className: "sep", "aria-hidden": "true" }, "·"));
    metaItems.push(el("span", {}, duration));
  }
  if (dateStr || timeStr) {
    metaItems.push(el("span", { className: "sep", "aria-hidden": "true" }, "·"));
    metaItems.push(el("span", { className: "session-time" }, [dateStr, timeStr].filter(Boolean).join(" ")));
  }

  const meta = el("div", { className: "session-meta" }, ...metaItems);
  const summary = buildActivitySummary(acts);

  const card = el("li", { className: "session-card" },
    el("div", { className: "session-header" },
      el("span", { className: "session-server" }, serverLabel),
      buildStatePill(state),
    ),
    meta,
    ...(summary ? [summary] : []),
  );

  return card;
}

// --- render session list -------------------------------------

function render(data) {
  sessionList.innerHTML = "";

  const sessions = data.sessions || [];

  if (sessions.length === 0) {
    trustPanel.hidden = false;
    return;
  }
  trustPanel.hidden = true;

  for (const session of sessions) {
    sessionList.append(buildSessionCard(session));
  }

  const skipped = data.skipped_lines || 0;
  if (skipped > 0) {
    skippedBadge.textContent = `${skipped} line${skipped !== 1 ? "s" : ""} skipped`;
    skippedBadge.hidden = false;
  } else {
    skippedBadge.hidden = true;
  }
}

// --- data fetching -------------------------------------------

async function loadSessions(fixture) {
  try {
    const url = fixture
      ? `/api/sessions?fixture=${encodeURIComponent(fixture)}`
      : "/api/sessions";
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    render(await res.json());
  } catch (err) {
    sessionList.innerHTML = "";
    trustPanel.hidden = true;
    sessionList.append(
      el("li", { className: "session-card" },
        el("p", { style: "color:var(--c-err);font-size:13px" },
          "Could not load trace data: " + err.message,
        ),
      ),
    );
  }
}

async function loadFixtures() {
  const res = await fetch("/api/fixtures");
  const data = await res.json();
  fixtureSelect.innerHTML = "";
  for (const name of data.fixtures) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === data.default) opt.selected = true;
    fixtureSelect.append(opt);
  }
  return fixtureSelect.value;
}

fixtureSelect.addEventListener("change", (e) => loadSessions(e.target.value));

(async () => {
  const defaultFixture = await loadFixtures();
  await loadSessions(defaultFixture);
})();

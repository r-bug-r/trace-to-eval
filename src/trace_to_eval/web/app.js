const tokenMeta = document.querySelector('meta[name="csrf-token"]');
const token = tokenMeta ? tokenMeta.getAttribute("content") || "" : "";
const main = document.getElementById("main");
const toast = document.getElementById("toast");

let flashMessage = null;
let toastTimer = null;
let jobTimer = null;
let renderSerial = 0;

// --------------------------------------------------------------------- DOM
function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class" || key === "className") {
      element.className = value;
    } else if (key === "text") {
      element.textContent = value;
    } else if (key === "dataset") {
      Object.assign(element.dataset, value);
    } else if (key === "value") {
      element.value = value;
    } else if (
      key === "checked" ||
      key === "selected" ||
      key === "disabled" ||
      key === "hidden" ||
      key === "open" ||
      key === "required"
    ) {
      element[key] = Boolean(value);
    } else if (key.startsWith("on") && typeof value === "function") {
      element.addEventListener(key.slice(2).toLowerCase(), value);
    } else {
      element.setAttribute(key, value === true ? "" : String(value));
    }
  }
  appendChildren(element, children);
  return element;
}

function appendChildren(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) {
      appendChildren(parent, child);
    } else if (child instanceof Node) {
      parent.append(child);
    } else {
      parent.append(document.createTextNode(String(child)));
    }
  }
}

function code(value) {
  return h("code", { text: value === null || value === undefined ? "—" : String(value) });
}

function text(value) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function truncate(value, length = 90) {
  const string = value === null || value === undefined ? "" : String(value);
  return string.length > length ? `${string.slice(0, length - 1)}…` : string;
}

// ------------------------------------------------------------------- format
const dateFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return dateFormatter.format(date);
}

function fmtPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function fmtPoints(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const points = Number(value) * 100;
  return `${points >= 0 ? "+" : ""}${points.toFixed(1)} pts`;
}

function fmtPctDelta(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${number >= 0 ? "+" : ""}${number.toFixed(1)}%`;
}

function fmtCost(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `$${Number(value).toFixed(6)}`;
}

function fmtMs(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return number >= 1000 ? `${(number / 1000).toFixed(2)} s` : `${number.toFixed(0)} ms`;
}

function fmtNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat().format(Number(value));
}

function firstFailure(result) {
  if (!result) return null;
  if (result.status === "infrastructure_error") {
    return {
      kind: "infrastructure",
      name: result.error_class || "provider",
      message: result.error_message || "Provider failure",
    };
  }
  for (const assertion of result.assertions || []) {
    if (assertion.kind === "deterministic" && assertion.status === "fail") {
      return {
        kind: "assertion",
        name: assertion.name || "assertion",
        message: assertion.message || "Assertion failed",
      };
    }
  }
  return null;
}

function outputText(result) {
  if (!result) return "";
  if (result.output_json && typeof result.output_json.text === "string") {
    return result.output_json.text;
  }
  return result.output_text || "";
}

function statusChip(status) {
  const map = {
    pass: ["Pass", "pass"],
    fail: ["Fail", "fail"],
    infrastructure_error: ["Infrastructure", "infra"],
    running: ["Running", "running"],
    completed: ["Completed", "pass"],
    failed: ["Failed", "fail"],
    success: ["Success", "pass"],
    failure: ["Failure", "fail"],
    unknown: ["Unknown", "neutral"],
  };
  const [label, variant] = map[status] || [status || "Unknown", "neutral"];
  return h("span", { class: `chip ${variant}`, text: label });
}

function labelChip(label) {
  if (!label) return h("span", { class: "chip neutral plain", text: "Unlabeled" });
  return statusChip(label.label);
}

function tagList(tags) {
  if (!tags || tags.length === 0) return h("span", { class: "muted", text: "—" });
  return h(
    "span",
    {},
    tags.map((tag) => h("span", { class: "tag", text: tag })),
  );
}

// --------------------------------------------------------------------- API
async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && method !== "HEAD") headers["X-Trace-To-Eval-Token"] = token;
  const response = await fetch(path, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store",
    credentials: "same-origin",
  });
  const raw = await response.text();
  let data = {};
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = { error: raw.slice(0, 500) };
    }
  }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed with ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function showToast(message, isError = false) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3600);
}

function flash(message) {
  flashMessage = message;
}

function showFormError(form, message) {
  let target = form.querySelector(".form-error");
  if (!target) {
    target = h("p", { class: "form-error", role: "alert" });
    form.append(target);
  }
  target.textContent = message;
}

// ------------------------------------------------------------------ layout
function pageHead(eyebrow, title, lede, actions = null) {
  return h(
    "div",
    { class: "page-head" },
    h(
      "div",
      {},
      eyebrow ? h("p", { class: "eyebrow", text: eyebrow }) : null,
      h("h1", { text: title }),
      lede ? h("p", { class: "lede", text: lede }) : null,
    ),
    actions,
  );
}

function panel(title, ...children) {
  return h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", { text: title })),
    ...children,
  );
}

function panelWithAction(title, action, ...children) {
  return h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", { text: title }), action),
    ...children,
  );
}

function stat(label, value, detail) {
  return h(
    "div",
    { class: "stat" },
    h("p", { class: "stat-label", text: label }),
    h("p", { class: "stat-value", text: value }),
    detail ? h("p", { class: "stat-detail", text: detail }) : null,
  );
}

function detail(label, value) {
  return h(
    "div",
    { class: "detail" },
    h("dt", { text: label }),
    h("dd", {}, value instanceof Node ? value : text(value)),
  );
}

function field(labelText, control, hint = null) {
  return h(
    "div",
    { class: "field" },
    h("label", { for: control.id || "" }, labelText),
    control,
    hint ? h("p", { class: "hint", text: hint }) : null,
  );
}

function selectControl(id, options, value) {
  const select = h("select", { id });
  for (const [optionValue, label] of options) {
    const option = h("option", { value: optionValue, text: label });
    if (String(optionValue) === String(value)) option.selected = true;
    select.append(option);
  }
  return select;
}

function emptyState(title, body, actions = null) {
  return h(
    "div",
    { class: "empty" },
    h("h2", { text: title }),
    h("p", { text: body }),
    actions ? h("div", { class: "button-row", style: null }, actions) : null,
  );
}

function banner(kind, title, body) {
  return h(
    "div",
    { class: `banner ${kind}` },
    h("div", {}, h("strong", { text: title }), h("p", { text: body })),
  );
}

function codeBlock(value, compact = false) {
  return h("pre", { class: `code-block${compact ? " compact" : ""}`, text: value });
}

function jsonDetails(label, value) {
  return h(
    "details",
    {},
    h("summary", { text: label }),
    codeBlock(JSON.stringify(value, null, 2)),
  );
}

function dataTable(caption, columns, rows, emptyText = "No rows.") {
  const table = h("table");
  table.append(h("caption", { class: "visually-hidden", text: caption }));
  const head = h("tr");
  for (const column of columns) {
    head.append(
      h(
        "th",
        { scope: "col", class: column.num ? "num" : "" },
        column.label,
      ),
    );
  }
  table.append(h("thead", {}, head));
  const body = h("tbody");
  if (rows.length === 0) {
    body.append(
      h(
        "tr",
        {},
        h("td", { colspan: String(columns.length), class: "table-empty", text: emptyText }),
      ),
    );
  } else {
    for (const row of rows) {
      const tr = h("tr");
      if (row.search) tr.dataset.search = row.search.toLowerCase();
      if (row.dataset) Object.assign(tr.dataset, row.dataset);
      row.cells.forEach((cell, index) => {
        const td = h("td", { class: cell && cell.class ? cell.class : "" });
        td.dataset.label = columns[index] ? columns[index].label : "";
        if (cell && cell.node instanceof Node) td.append(cell.node);
        else td.append(document.createTextNode(text(cell)));
        tr.append(td);
      });
      body.append(tr);
    }
  }
  table.append(body);
  return h("div", { class: "table-wrap" }, table);
}

function metricCells(items) {
  return h(
    "dl",
    { class: "detail-grid" },
    items.map(([label, value]) => detail(label, value)),
  );
}

// ------------------------------------------------------------------ router
function parseRoute() {
  const raw = location.hash.startsWith("#") ? location.hash.slice(1) : location.hash;
  const [pathPart, queryPart = ""] = raw.split("?");
  const path = pathPart || "/";
  return { path, query: new URLSearchParams(queryPart) };
}

function setActiveNav(path) {
  const active = path === "/" ? "/" : `/${path.split("/")[1] || ""}`;
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

async function render({ focusHeading = true } = {}) {
  if (jobTimer) {
    clearTimeout(jobTimer);
    jobTimer = null;
  }
  const openDialog = document.querySelector("dialog[open]");
  if (openDialog) openDialog.close();
  const serial = ++renderSerial;
  const route = parseRoute();
  setActiveNav(route.path);
  main.setAttribute("aria-busy", "true");
  main.replaceChildren(loadingView(route));
  try {
    const view = await routeView(route);
    if (serial !== renderSerial) return;
    main.replaceChildren(view);
    const heading = focusHeading ? view.querySelector("h1") : null;
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
    window.scrollTo({ top: 0, behavior: "instant" });
  } catch (error) {
    if (serial !== renderSerial) return;
    main.replaceChildren(errorView(error));
  } finally {
    if (serial === renderSerial) main.removeAttribute("aria-busy");
  }
  if (flashMessage) {
    showToast(flashMessage);
    flashMessage = null;
  }
}

function errorView(error) {
  return h(
    "div",
    { class: "view" },
    pageHead("Error", "Could not load this view", error && error.message ? error.message : "Unknown error."),
    h(
      "div",
      { class: "button-row" },
      h("button", { class: "button primary", type: "button", onclick: () => render() }, "Retry"),
      h("a", { class: "button", href: "#/" }, "Overview"),
    ),
  );
}

function routeTitle(route) {
  const path = route.path.length > 1 ? route.path.replace(/\/+$/, "") : "/";
  if (path === "/" || path === "") return ["Overview", "Local regression lab"];
  if (path === "/cases") return ["Cases", "Cases"];
  if (path.startsWith("/cases/")) return ["Case", "Case detail"];
  if (path === "/runs") return ["Runs", "Runs"];
  if (path.startsWith("/runs/")) return ["Run", "Run detail"];
  if (path === "/ci") return ["CI", "CI setup"];
  if (path === "/settings") return ["Settings", "Settings"];
  return ["Not found", "Not found"];
}

function loadingView(route) {
  const [eyebrow, title] = routeTitle(route);
  return h(
    "div",
    { class: "view" },
    pageHead(eyebrow, title),
    h("p", { class: "loading", role: "status", text: "Loading…" }),
  );
}

async function routeView(route) {
  const path = route.path.length > 1 ? route.path.replace(/\/+$/, "") : "/";
  if (path === "/" || path === "") return overviewView();
  if (path === "/cases") return casesView();
  if (path.startsWith("/cases/")) {
    return caseDetailView(decodeURIComponent(path.slice("/cases/".length)), route.query);
  }
  if (path === "/runs") return runsView(route.query);
  if (path.startsWith("/runs/")) {
    return runDetailView(decodeURIComponent(path.slice("/runs/".length)), route.query);
  }
  if (path === "/ci") return ciView(route.query);
  if (path === "/settings") return settingsView();
  return errorView(new Error(`Unknown route: ${path}`));
}

// ----------------------------------------------------------------- overview
async function overviewView() {
  const [casesData, runsData] = await Promise.all([
    api("/api/cases?limit=5"),
    api("/api/runs?limit=5"),
  ]);
  const cases = casesData.cases || [];
  const runs = runsData.runs || [];
  const latest = runs[0] || null;
  const summary = latest ? latest.summary : null;
  const failures = summary ? Number(summary.failed || 0) + Number(summary.infrastructure_errors || 0) : null;

  return h(
    "div",
    { class: "view" },
    h(
      "section",
      { class: "hero" },
      h(
        "div",
        { class: "hero-main" },
        h("p", { class: "eyebrow", text: "Local regression lab" }),
        h("h1", { text: "Every important AI failure becomes a repeatable test." }),
        h(
          "p",
          { class: "lede" },
          "Import real interactions, label what went wrong, and replay them against a deterministic fake model. Quality, cost, and latency gates turn regressions into exit codes.",
        ),
        h(
          "div",
          { class: "button-row" },
          h("a", { class: "button primary", href: "#/runs" }, "Run a replay"),
          h("a", { class: "button", href: "#/cases" }, "Browse cases"),
        ),
      ),
      h(
        "div",
        { class: "hero-side" },
        h("p", { class: "eyebrow", text: "Start with the CLI" }),
        codeBlock("trace-to-eval import examples/traces/*.jsonl\ntrace-to-eval label case_… --label failure --category quality\ntrace-to-eval run --provider fake --save-baseline main"),
        h(
          "p",
          { class: "small muted" },
          "The UI never starts live providers. Live calls remain an explicit CLI action with hard caps.",
        ),
        h("a", { class: "small", href: "#/settings" }, "Inspect local settings"),
      ),
    ),
    h(
      "div",
      { class: "grid stats" },
      stat("Cases", fmtNumber(casesData.total || cases.length), "Normalized and redacted"),
      stat("Runs", fmtNumber(runs.length), "Most recent stored runs"),
      stat("Latest pass rate", summary ? fmtPct(summary.pass_rate) : "—", latest ? latest.run_id : "No runs yet"),
      stat("Latest failures", failures === null ? "—" : fmtNumber(failures), summary ? `${summary.passed || 0} passed` : "Run the suite to populate"),
    ),
    h(
      "div",
      { class: "split" },
      panelWithAction(
        "Recent cases",
        h("a", { href: "#/cases" }, "View all"),
        cases.length === 0
          ? emptyState(
              "No cases yet",
              "Import a trace file to create your first content-addressed case. Redaction runs before persistence.",
              h("code", { class: "code-block compact" }, "trace-to-eval import examples/traces/chat.jsonl"),
            )
          : recentCases(cases),
      ),
      panelWithAction(
        "Latest runs",
        h("a", { href: "#/runs" }, "View all"),
        runs.length === 0
          ? emptyState(
              "No runs yet",
              "A run replays selected cases, evaluates deterministic assertions, and stores a gate-ready summary.",
              h("a", { class: "button", href: "#/runs" }, "Open run form"),
            )
          : recentRuns(runs),
      ),
    ),
    panel(
      "How it works",
      h(
        "div",
        { class: "steps" },
        h(
          "article",
          { class: "step" },
          h("h3", { text: "Import" }),
          h("p", { text: "JSON or JSONL traces are validated, normalized, redacted, and stored in local SQLite with a content-addressed ID." }),
        ),
        h(
          "article",
          { class: "step" },
          h("h3", { text: "Label and assert" }),
          h("p", { text: "Attach human labels and deterministic expectations. Labels are append-only; assertion edits keep the stable case identity." }),
        ),
        h(
          "article",
          { class: "step" },
          h("h3", { text: "Replay and gate" }),
          h("p", { text: "Fake-provider runs are repeatable. Compare against a baseline and fail CI on quality, cost, latency, or infrastructure regressions." }),
        ),
      ),
    ),
  );
}

function recentCases(cases) {
  return h(
    "ul",
    { class: "recent-list" },
    cases.map((item) =>
      h(
        "li",
        { class: "recent-item" },
        h(
          "div",
          { class: "recent-head" },
          h("a", { href: `#/cases/${encodeURIComponent(item.case_id)}`, class: "mono small" }, item.case_id),
          labelChip(item.label),
        ),
        h("p", { class: "recent-preview", text: truncate(item.preview, 140) }),
        h(
          "p",
          { class: "recent-meta" },
          tagList(item.tags),
          h("span", { text: (item.model && item.model.name) || "—" }),
          h("span", { text: fmtDate(item.imported_at) }),
        ),
      ),
    ),
  );
}

function caseTable(cases) {
  return dataTable(
    "Cases",
    [
      { label: "Case" },
      { label: "Label" },
      { label: "Tags" },
      { label: "Preview" },
      { label: "Model" },
      { label: "Imported" },
    ],
    cases.map((item) => ({
      search: [item.case_id, item.preview, (item.tags || []).join(" "), item.label ? item.label.label : ""].join(" "),
      cells: [
        { node: h("a", { href: `#/cases/${encodeURIComponent(item.case_id)}` }, code(item.case_id)) },
        { node: labelChip(item.label) },
        { node: tagList(item.tags) },
        { node: h("span", { text: truncate(item.preview, 110) }) },
        { node: h("span", { class: "mono small", text: (item.model && item.model.name) || "—" }) },
        { node: h("span", { class: "nowrap small", text: fmtDate(item.imported_at) }) },
      ],
    })),
    "No cases.",
  );
}

function recentRuns(runs) {
  return h(
    "ul",
    { class: "recent-list" },
    runs.map((run) => {
      const summary = run.summary || {};
      return h(
        "li",
        { class: "recent-item" },
        h(
          "div",
          { class: "recent-head" },
          h("a", { href: `#/runs/${encodeURIComponent(run.run_id)}`, class: "mono small" }, run.run_id),
          statusChip(run.status),
        ),
        h(
          "p",
          { class: "recent-meta" },
          h("span", { text: `pass ${summary.pass_rate === undefined ? "—" : fmtPct(summary.pass_rate)}` }),
          h("span", { text: `cost ${fmtCost(summary.avg_cost_usd)}` }),
          h("span", { text: `p95 ${fmtMs(summary.p95_latency_ms)}` }),
          h("span", { text: fmtDate(run.started_at) }),
        ),
      );
    }),
  );
}

function runTable(runs) {
  return dataTable(
    "Runs",
    [
      { label: "Run" },
      { label: "Status" },
      { label: "Pass rate", num: true },
      { label: "Avg cost", num: true },
      { label: "p95", num: true },
      { label: "Baseline" },
      { label: "Started" },
    ],
    runs.map((run) => {
      const summary = run.summary || {};
      const delta = run.deltas || {};
      const baselineText = run.baseline && run.baseline.unavailable
        ? "unavailable"
        : delta.available
          ? `pass ${fmtPoints(delta.pass_rate)} · cost ${fmtPctDelta(delta.cost_pct)}`
          : "—";
      return {
        search: [run.run_id, run.model, run.baseline_name, run.status].join(" "),
        cells: [
          { node: h("a", { href: `#/runs/${encodeURIComponent(run.run_id)}` }, code(run.run_id)) },
          { node: statusChip(run.status) },
          { node: h("span", { class: "num", text: summary.pass_rate === undefined ? "—" : fmtPct(summary.pass_rate) }) },
          { node: h("span", { class: "num", text: fmtCost(summary.avg_cost_usd) }) },
          { node: h("span", { class: "num", text: fmtMs(summary.p95_latency_ms) }) },
          { node: h("span", { class: "small", text: baselineText }) },
          { node: h("span", { class: "nowrap small", text: fmtDate(run.started_at) }) },
        ],
      };
    }),
    "No runs yet.",
  );
}

// -------------------------------------------------------------------- cases
async function casesView() {
  const data = await api("/api/cases?limit=500");
  const cases = data.cases || [];
  const total = data.total || cases.length;
  const countLabel = h("span", { class: "muted small", text: `Showing ${cases.length} of ${total} cases` });

  if (cases.length === 0) {
    return h(
      "div",
      { class: "view" },
      pageHead("Cases", "Cases", "A case is a normalized, redacted trace with a stable identity."),
      emptyState(
        "No cases stored",
        "Import a trace file with the CLI. The UI is read/write for labels and assertions, but imports stay in the CLI so redaction and validation are explicit.",
        h("code", { class: "code-block compact" }, "trace-to-eval import examples/traces/*.jsonl"),
      ),
    );
  }

  const search = h("input", {
    id: "case-search",
    type: "search",
    placeholder: "Case ID, tag, model, label, preview…",
    autocomplete: "off",
  });
  const table = caseTable(cases);
  const toolbar = h(
    "div",
    { class: "toolbar" },
    h("div", { class: "field grow" }, h("label", { for: "case-search", text: "Search cases" }), search),
    countLabel,
  );
  search.addEventListener("input", () => {
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    for (const row of table.querySelectorAll("tbody tr[data-search]")) {
      const match = !query || row.dataset.search.includes(query);
      row.hidden = !match;
      if (match) visible += 1;
    }
    countLabel.textContent = query
      ? `${visible} matching case${visible === 1 ? "" : "s"} of ${total}`
      : `Showing ${cases.length} of ${total} cases`;
  });

  return h(
    "div",
    { class: "view" },
    pageHead("Cases", "Cases", "A case is a normalized, redacted trace with a stable identity. Open one to label it, edit text assertions, and inspect replay history."),
    h("section", { class: "panel" }, toolbar, table),
  );
}

async function caseDetailView(caseId, query) {
  const runId = query.get("run");
  const baseline = query.get("baseline");
  const params = new URLSearchParams();
  if (runId) params.set("run", runId);
  if (baseline) params.set("baseline", baseline);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const data = await api(`/api/cases/${encodeURIComponent(caseId)}${suffix}`);
  const item = data.case;
  const result = data.result;
  const trace = data.trace || {};
  const run = data.run;
  const source = item.source || {};
  const model = item.model || {};
  const redaction = item.redaction || { total: 0, by_category: {} };

  const actions = h(
    "div",
    { class: "button-row" },
    h(
      "button",
      {
        class: "button primary",
        type: "button",
        onclick: (event) => runSingleCase(caseId, event.currentTarget),
      },
      "Run this case",
    ),
    h(
      "button",
      {
        class: "button danger",
        type: "button",
        onclick: () => openDeleteDialog(caseId),
      },
      "Delete case",
    ),
  );

  const resultPanel = result
    ? resultPanelView(data)
    : panel(
        "Latest result",
        emptyState(
          "Not replayed yet",
          "Run this case against the deterministic fake provider to evaluate its assertions and store a result.",
          h(
            "button",
            {
              class: "button",
              type: "button",
              onclick: (event) => runSingleCase(caseId, event.currentTarget),
            },
            "Run this case",
          ),
        ),
      );

  return h(
    "div",
    { class: "view" },
    h("p", { class: "small" }, h("a", { href: "#/cases" }, "← All cases")),
    pageHead("Case", "Case detail", `Stable identity ${caseId}`, actions),
    h(
      "div",
      { class: "button-row" },
      labelChip(item.label),
      statusChip(result ? result.status : "unknown"),
      h("span", { class: "muted small", text: `Trace ${item.trace_id || "—"}` }),
      h("span", { class: "muted small", text: `Imported ${fmtDate(item.imported_at)}` }),
    ),
    h(
      "section",
      { class: "panel" },
      h("h2", { text: "Provenance" }),
      metricCells([
        ["Source", [source.app, source.environment].filter(Boolean).join(" / ") || "—"],
        ["Model", model.name || "—"],
        ["Provider", model.provider || "—"],
        ["Created", fmtDate(item.created_at)],
        ["Messages", fmtNumber(item.message_count)],
        ["Tool calls", fmtNumber(item.tool_call_count)],
        ["Redactions", `${redaction.total || 0}`],
        ["Content hash", code(item.content_hash || "—")],
        ["Source path", item.source_path || "—"],
      ]),
      redaction.total
        ? h(
            "p",
            { class: "small muted" },
            `Redacted categories: ${Object.entries(redaction.by_category || {})
              .map(([name, count]) => `${name} (${count})`)
              .join(", ")}`,
          )
        : null,
    ),
    h(
      "div",
      { class: "split" },
      labelPanel(item, data.failure_categories || []),
      assertionsPanel(caseId, data.editable_assertions || []),
    ),
    resultPanel,
    baselinePanel(data),
    tracePanel(trace, item, result, item.label),
    historyPanel(item, data.history || [], run),
    labelsPanel(data.labels || []),
    panel("Raw trace", jsonDetails("Show normalized trace JSON", trace)),
  );
}

function labelPanel(item, categories) {
  const current = item.label || {};
  const labelSelect = selectControl(
    "label-value",
    [
      ["", "Choose a label"],
      ["success", "success"],
      ["failure", "failure"],
      ["unknown", "unknown"],
    ],
    current.label || "",
  );
  labelSelect.required = true;
  const categorySelect = selectControl(
    "label-category",
    [["", "No category"], ...categories.map((category) => [category, category])],
    current.category || "",
  );
  const notes = h("textarea", {
    id: "label-notes",
    maxlength: "2000",
    rows: "3",
    placeholder: "What happened? Keep it factual and short.",
  });
  notes.value = current.notes || "";
  const form = h(
    "form",
    {
      onsubmit: async (event) => {
        event.preventDefault();
        const submit = form.querySelector("button[type=submit]");
        submit.disabled = true;
        try {
          await api(`/api/cases/${encodeURIComponent(item.case_id)}/label`, {
            method: "POST",
            body: {
              label: labelSelect.value,
              category: categorySelect.value || null,
              notes: notes.value.trim() || null,
            },
          });
          flash("Label saved.");
          await render();
        } catch (error) {
          showFormError(form, error.message);
          submit.disabled = false;
        }
      },
    },
    h("div", { class: "form-grid" }, field("Label", labelSelect), field("Failure category", categorySelect)),
    field("Notes", notes, "Labels are append-only; this adds a new label entry."),
    h(
      "div",
      { class: "button-row" },
      h("button", { class: "button primary", type: "submit" }, "Save label"),
      current.created_at
        ? h("span", { class: "small muted", text: `Latest label ${fmtDate(current.created_at)}` })
        : null,
    ),
  );
  return panel("Human label", form);
}

function assertionsPanel(caseId, assertions) {
  const rows = h("div", { class: "assertions" });
  const form = h("form", {
    onsubmit: async (event) => {
      event.preventDefault();
      const submit = form.querySelector("button[type=submit]");
      submit.disabled = true;
      const payload = [];
      for (const row of rows.querySelectorAll(".assertion-row")) {
        const type = row.querySelector("select").value;
        const value = row.querySelector("input").value.trim();
        if (value) payload.push({ type, value });
      }
      try {
        await api(`/api/cases/${encodeURIComponent(caseId)}/assertions`, {
          method: "POST",
          body: { assertions: payload },
        });
        flash("Assertions saved. The stable case ID is unchanged.");
        await render();
      } catch (error) {
        showFormError(form, error.message);
        submit.disabled = false;
      }
    },
  });

  const addRow = (type = "text_contains", value = "") => {
    if (rows.querySelectorAll(".assertion-row").length >= 50) {
      showToast("At most 50 editable assertions.", true);
      return;
    }
    const typeSelect = selectControl(
      `assertion-type-${Date.now()}-${Math.random()}`,
      [
        ["text_contains", "text_contains"],
        ["text_not_contains", "text_not_contains"],
        ["text_equals", "text_equals"],
      ],
      type,
    );
    typeSelect.setAttribute("aria-label", "Assertion type");
    const valueInput = h("input", {
      type: "text",
      value,
      maxlength: "1000",
      placeholder: "Expected text",
      "aria-label": "Assertion value",
    });
    const remove = h(
      "button",
      {
        class: "button small",
        type: "button",
        onclick: () => row.remove(),
      },
      "Remove",
    );
    const row = h(
      "div",
      { class: "assertion-row" },
      typeSelect,
      valueInput,
      remove,
    );
    rows.append(row);
  };

  if (assertions.length === 0) {
    addRow();
  } else {
    for (const assertion of assertions) addRow(assertion.type, assertion.value);
  }

  form.append(
    rows,
    h(
      "div",
      { class: "button-row" },
      h("button", { class: "button small", type: "button", onclick: () => addRow() }, "Add assertion"),
      h("button", { class: "button primary", type: "submit" }, "Save assertions"),
    ),
    h(
      "p",
      { class: "small muted" },
      "Only text assertions are editable here. JSON Schema, tool-call, citation, and human-label expectations are preserved unchanged. Saving updates the content hash but keeps the stable case ID.",
    ),
  );
  return panel("Deterministic assertions", form);
}

function resultPanelView(data) {
  const result = data.result;
  const failure = firstFailure(result);
  const rubric = result.rubric;
  const agreement = result.label_agreement;
  return panel(
    "Latest result",
    h(
      "div",
      { class: "button-row" },
      statusChip(result.status),
      h("span", { class: "small muted", text: `Run ${result.run_id}` }),
      data.run ? h("a", { class: "small", href: `#/runs/${encodeURIComponent(data.run.run_id)}` }, "Open run") : null,
      h("span", { class: "small muted", text: fmtDate(result.started_at || result.run_started_at) }),
    ),
    failure
      ? banner(
          failure.kind === "infrastructure" ? "warn" : "fail",
          failure.kind === "infrastructure" ? "Infrastructure failure" : "Assertion failed",
          `${failure.name}: ${failure.message}`,
        )
      : null,
    data.divergence
      ? h(
          "div",
          { class: "divergence" },
          h("div", {}, h("h3", { text: "Baseline" }), h("pre", { text: text(data.divergence.baseline) })),
          h("div", {}, h("h3", { text: "Candidate" }), h("pre", { text: text(data.divergence.candidate) })),
        )
      : null,
    metricCells([
      ["Cost", fmtCost(result.cost_usd)],
      ["Latency", fmtMs(result.latency_ms)],
      ["Human label", result.human_label || "—"],
      ["Label agreement", agreement === null || agreement === undefined ? "—" : agreement ? "Agrees" : "Disagrees"],
      ["Rubric score", rubric && rubric.score !== undefined ? String(rubric.score) : "—"],
      ["Rubric model", rubric && rubric.evaluator_version ? String(rubric.evaluator_version) : "—"],
    ]),
    h("h3", { text: "Output" }),
    h("pre", { class: "output", text: outputText(result) || "—" }),
    h("h3", { text: "Assertions" }),
    dataTable(
      "Assertion results",
      [
        { label: "Name" },
        { label: "Kind" },
        { label: "Status" },
        { label: "Detail" },
      ],
      (result.assertions || []).map((assertion) => ({
        cells: [
          { node: code(assertion.name || "—") },
          { node: h("span", { class: "small", text: assertion.kind || "—" }) },
          { node: statusChip(assertion.status) },
          { node: h("span", { class: "small", text: assertion.message || "—" }) },
        ],
      })),
      "No assertions recorded.",
    ),
    rubric ? jsonDetails("Show rubric detail", rubric) : null,
  );
}

function baselinePanel(data) {
  const baseline = data.baseline;
  if (!baseline) {
    return panel(
      "Baseline comparison",
      h("p", { class: "muted" }, "This run has no baseline. Save a baseline from the run form or pass one when opening a run."),
    );
  }
  if (baseline.unavailable) {
    return panel(
      "Baseline comparison",
      banner(
        "warn",
        "Baseline unavailable",
        `Baseline “${baseline.name}” is referenced by this run but no longer exists in the store. Re-save it from a completed run.`,
      ),
    );
  }
  const deltas = data.deltas || {};
  return panel(
    "Baseline comparison",
    h(
      "div",
      { class: "button-row" },
      h("span", { class: "small muted", text: `Baseline ${baseline.name || baseline.run_id}` }),
      baseline.run_id ? h("a", { class: "small", href: `#/runs/${encodeURIComponent(baseline.run_id)}` }, "Open baseline run") : null,
    ),
    deltas.available
      ? metricCells([
          ["Pass rate", fmtPoints(deltas.pass_rate)],
          ["Cost", fmtPctDelta(deltas.cost_pct)],
          ["p95 latency", fmtPctDelta(deltas.latency_pct)],
        ])
      : h("p", { class: "muted" }, "Baseline metrics are unavailable."),
    data.baseline_result
      ? h(
          "div",
          { class: "divergence" },
          h("div", {}, h("h3", { text: "Baseline output" }), h("pre", { text: outputText(data.baseline_result) || "—" })),
          h("div", {}, h("h3", { text: "Candidate output" }), h("pre", { text: outputText(data.result) || "—" })),
        )
      : null,
  );
}

const EVENT_KINDS = {
  system: ["System instruction", "system"],
  developer: ["Developer instruction", "system"],
  user: ["User input", "user"],
  assistant: ["Model response", "assistant"],
  tool: ["Tool result", "tool-result"],
};

function eventKind(role) {
  return EVENT_KINDS[role] || ["Message", "message"];
}

function toolCallItem(call, { withKind = true } = {}) {
  const name = call.function ? call.function.name : call.name || "tool";
  const args = call.function ? call.function.arguments : call.arguments;
  const callId = call.id || call.tool_call_id;
  return h(
    "li",
    { class: "tool-event" },
    h(
      "div",
      { class: "event-meta" },
      withKind ? h("span", { class: "event-kind", text: "Tool call" }) : null,
      h("span", { class: "mono small", text: name }),
      callId ? h("span", { class: "event-label mono", text: callId }) : null,
    ),
    args === undefined
      ? null
      : h("pre", { class: "code-block compact", text: typeof args === "string" ? args : JSON.stringify(args, null, 2) }),
  );
}

function assertionEvidence(result) {
  if (!result) return null;
  const assertions = result.assertions || [];
  if (assertions.length === 0) return null;
  const passed = assertions.filter((item) => item.status === "pass").length;
  return h(
    "div",
    { class: "evidence" },
    h(
      "div",
      { class: "evidence-head" },
      h("h3", { text: "Pass/fail evidence" }),
      h("span", { class: "small muted", text: `${passed} of ${assertions.length} assertions passed · run ${result.run_id}` }),
    ),
    h(
      "ul",
      { class: "evidence-list" },
      assertions.map((item) =>
        h(
          "li",
          { class: `evidence-item ${item.status === "pass" ? "pass" : "fail"}` },
          statusChip(item.status),
          h("span", { class: "mono small", text: item.name || "—" }),
          h("span", { class: "small muted", text: item.message || "" }),
        ),
      ),
    ),
  );
}

function traceMeta(trace, item) {
  const model = trace.model || item.model || {};
  const metrics = trace.metrics || {};
  const redaction = item.redaction || { total: 0 };
  return h(
    "div",
    { class: "trace-meta" },
    h("span", { class: "mono small", text: `${model.provider || "unknown"} / ${model.name || "unknown"}` }),
    trace.created_at ? h("span", { class: "small muted", text: fmtDate(trace.created_at) }) : null,
    metrics.latency_ms !== undefined ? h("span", { class: "small muted", text: `trace latency ${fmtMs(metrics.latency_ms)}` }) : null,
    redaction.total
      ? h("span", { class: "chip warn plain", text: `${redaction.total} value(s) redacted` })
      : h("span", { class: "chip neutral plain", text: "No redactions" }),
    h("span", { class: "small muted", text: "Per-event timings are not stored in this trace schema." }),
  );
}

function tracePanel(trace, item, result, label) {
  const events = [];
  const input = trace.input || {};
  const output = trace.output || {};
  for (const message of input.messages || []) {
    const content = typeof message.content === "string" ? message.content : JSON.stringify(message.content);
    const isLong = content && content.length > 400;
    const body = isLong
      ? h("details", {}, h("summary", { text: "Show full message" }), h("pre", { text: content }))
      : h("p", { text: content || "—" });
    const [kindLabel, kindClass] = eventKind(message.role);
    events.push(
      h(
        "li",
        { class: `event ${kindClass}` },
        h(
          "div",
          { class: "event-meta" },
          h("span", { class: "event-kind", text: kindLabel }),
          message.tool_call_id ? h("span", { class: "event-label mono", text: message.tool_call_id }) : null,
        ),
        body,
        (message.tool_calls || []).length
          ? h("ul", { class: "tool-events" }, (message.tool_calls || []).map((call) => toolCallItem(call)))
          : null,
      ),
    );
  }
  if (output.text || (output.tool_calls || []).length) {
    events.push(
      h(
        "li",
        { class: "event assistant" },
        h(
          "div",
          { class: "event-meta" },
          h("span", { class: "event-kind", text: output.text ? "Model output" : "Model response" }),
          output.finish_reason ? h("span", { class: "event-label", text: `finish_reason ${output.finish_reason}` }) : null,
        ),
        output.text ? h("pre", { text: output.text }) : null,
        (output.tool_calls || []).length
          ? h(
              "ul",
              { class: "tool-events" },
              (output.tool_calls || []).map((call) => toolCallItem(call, { withKind: Boolean(output.text) })),
            )
          : null,
      ),
    );
  }
  if (result) {
    const evidence = assertionEvidence(result);
    events.push(
      h(
        "li",
        { class: "event evaluator" },
        h("div", { class: "event-meta" }, h("span", { class: "event-kind", text: "Evaluator" }), statusChip(result.status)),
        evidence || h("p", { class: "muted small" }, "No assertions were evaluated for this run."),
      ),
    );
  }
  if (label && label.label) {
    events.push(
      h(
        "li",
        { class: "event human-label" },
        h(
          "div",
          { class: "event-meta" },
          h("span", { class: "event-kind", text: "Human label" }),
          labelChip(label),
          label.category ? h("span", { class: "event-label", text: label.category }) : null,
        ),
        label.notes ? h("p", { class: "small" }, label.notes) : null,
      ),
    );
  }
  return panelWithAction(
    "Trace timeline",
    traceMeta(trace, item),
    events.length
      ? h("ol", { class: "timeline" }, events)
      : h("p", { class: "muted" }, "No messages or output in this trace."),
  );
}

function historyPanel(item, history, selectedRun) {
  return panelWithAction(
    "Replay history",
    selectedRun ? h("span", { class: "small muted", text: `Selected ${selectedRun.run_id}` }) : null,
    dataTable(
      "Replay history",
      [
        { label: "Run" },
        { label: "Started" },
        { label: "Status" },
        { label: "Model" },
        { label: "Cost", num: true },
        { label: "Latency", num: true },
        { label: "Label" },
      ],
      history.map((result) => ({
        cells: [
          {
            node: h(
              "a",
              { href: `#/cases/${encodeURIComponent(item.case_id)}?run=${encodeURIComponent(result.run_id)}` },
              code(result.run_id),
            ),
          },
          { node: h("span", { class: "nowrap small", text: fmtDate(result.run_started_at) }) },
          { node: statusChip(result.status) },
          { node: h("span", { class: "mono small", text: result.run_model || "—" }) },
          { node: h("span", { class: "num", text: fmtCost(result.cost_usd) }) },
          { node: h("span", { class: "num", text: fmtMs(result.latency_ms) }) },
          { node: h("span", { class: "small", text: result.human_label || "—" }) },
        ],
      })),
      "No replay results for this case yet.",
    ),
  );
}

function labelsPanel(labels) {
  return panel(
    "Label history",
    dataTable(
      "Label history",
      [
        { label: "When" },
        { label: "Label" },
        { label: "Category" },
        { label: "Notes" },
      ],
      labels
        .slice()
        .reverse()
        .map((label) => ({
          cells: [
            { node: h("span", { class: "nowrap small", text: fmtDate(label.created_at) }) },
            { node: statusChip(label.label) },
            { node: h("span", { class: "small", text: label.category || "—" }) },
            { node: h("span", { class: "small", text: label.notes || "—" }) },
          ],
        })),
      "No labels recorded.",
    ),
  );
}

async function runSingleCase(caseId, button) {
  button.disabled = true;
  try {
    const data = await api("/api/run", {
      method: "POST",
      body: { case_ids: [caseId] },
    });
    flash("Replay started.");
    location.hash = `#/runs?job=${encodeURIComponent(data.job.id)}`;
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
  }
}

function openDeleteDialog(caseId) {
  const dialog = document.getElementById("delete-dialog");
  const idTarget = document.getElementById("delete-case-id");
  const cancel = document.getElementById("cancel-delete");
  const confirm = document.getElementById("confirm-delete");
  if (!dialog || !idTarget || !cancel || !confirm) return;
  idTarget.textContent = caseId;
  cancel.onclick = () => dialog.close();
  confirm.onclick = async () => {
    confirm.disabled = true;
    try {
      await api(`/api/cases/${encodeURIComponent(caseId)}`, { method: "DELETE" });
      dialog.close();
      flash("Case deleted.");
      const before = location.hash;
      location.hash = "#/cases";
      if (location.hash === before) await render();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      confirm.disabled = false;
    }
  };
  dialog.showModal();
}

// --------------------------------------------------------------------- runs
async function runsView(query) {
  const [casesData, baselinesData, runsData, settings] = await Promise.all([
    api("/api/cases?limit=1"),
    api("/api/baselines"),
    api("/api/runs?limit=20"),
    api("/api/settings"),
  ]);
  const cases = casesData.cases || [];
  const baselines = baselinesData.baselines || [];
  const runs = runsData.runs || [];
  const jobId = query.get("job");
  const prefillCase = query.get("case");

  const head = pageHead(
    "Runs",
    "Runs",
    "Replay selected cases with the deterministic fake provider. Gates compare quality, cost, latency, and infrastructure failures against thresholds and a baseline.",
  );

  if (casesData.total === 0) {
    return h(
      "div",
      { class: "view" },
      head,
      emptyState(
        "No cases to replay",
        "Import traces first, then return here to run the suite.",
        h("code", { class: "code-block compact" }, "trace-to-eval import examples/traces/*.jsonl"),
      ),
      panelWithAction("Recent runs", h("a", { href: "#/cases" }, "Open cases"), runTable(runs)),
    );
  }

  const form = runForm(settings, baselines, prefillCase);
  const jobPanel = jobId ? jobPanelView() : null;
  const view = h(
    "div",
    { class: "view" },
    head,
    jobPanel,
    form,
    panelWithAction("Recent runs", h("a", { href: "#/cases" }, "Open cases"), runTable(runs)),
  );
  if (jobId && jobPanel) startJobPolling(jobId, jobPanel);
  return view;
}

function jobPanelView() {
  return h(
    "section",
    { class: "panel", id: "job-panel", "aria-live": "polite" },
    h("h2", { text: "Replay job" }),
    h("p", { class: "muted", text: "Waiting for progress…" }),
  );
}

function runForm(settings, baselines, prefillCase) {
  const thresholds = settings.thresholds || {};
  const form = h("form", { class: "panel" });
  form.append(h("div", { class: "panel-head" }, h("h2", { text: "Start a replay" })));
  const tags = h("input", { id: "run-tags", type: "text", placeholder: "tag-a, tag-b" });
  const caseIds = h("input", {
    id: "run-cases",
    type: "text",
    placeholder: "case_…, case_…",
    value: prefillCase || "",
  });
  const maxCases = h("input", { id: "run-max-cases", type: "number", min: "1", max: "1000", step: "1", placeholder: "All matching" });
  const baseline = selectControl(
    "run-baseline",
    [["", "No baseline"], ...baselines.map((item) => [item.name, item.name])],
    "",
  );
  const saveBaseline = h("input", {
    id: "run-save-baseline",
    type: "text",
    maxlength: "64",
    placeholder: "e.g. main",
  });
  const codeRevision = h("input", {
    id: "run-code-revision",
    type: "text",
    maxlength: "120",
    value: settings.code_revision || "",
    placeholder: "git SHA or label",
  });

  const thresholdDefs = [
    ["min_pass_rate", "Minimum pass rate", "0.85", "0–1"],
    ["max_avg_cost_usd", "Max average cost (USD)", "0.002", "Per case"],
    ["max_p95_latency_ms", "Max p95 latency (ms)", "1500", "Per run"],
    ["max_total_cost_usd", "Max total cost (USD)", "0.10", "Per run"],
    ["max_infrastructure_errors", "Max infrastructure errors", "0", "Integer"],
    ["max_signal_failures", "Max signal failures", "0", "Integer"],
    ["max_pass_rate_drop", "Max pass-rate drop", "0.05", "vs baseline"],
    ["max_cost_increase_pct", "Max cost increase %", "25", "vs baseline"],
    ["max_latency_increase_pct", "Max latency increase %", "25", "vs baseline"],
  ];
  const thresholdInputs = new Map();
  const thresholdGrid = h("div", { class: "form-grid three" });
  for (const [key, label, placeholder, hint] of thresholdDefs) {
    const input = h("input", {
      id: `threshold-${key}`,
      type: "number",
      step: key.startsWith("max_infrastructure") || key.startsWith("max_signal") ? "1" : "any",
      min: "0",
      placeholder,
      value: thresholds[key] === null || thresholds[key] === undefined ? "" : String(thresholds[key]),
    });
    thresholdInputs.set(key, input);
    thresholdGrid.append(field(label, input, hint));
  }

  const preview = h("pre", { class: "code-block" });
  const updatePreview = () => {
    const selected = [];
    if (caseIds.value.trim()) selected.push(`--case ${caseIds.value.trim().split(/[,\s]+/).filter(Boolean).join(" --case ")}`);
    if (tags.value.trim()) selected.push(`--tag ${tags.value.trim().split(/[,\s]+/).filter(Boolean).join(" --tag ")}`);
    if (maxCases.value) selected.push(`--max-cases ${maxCases.value}`);
    if (baseline.value) selected.push(`--baseline ${baseline.value}`);
    if (saveBaseline.value.trim()) selected.push(`--save-baseline ${saveBaseline.value.trim()}`);
    preview.textContent = `trace-to-eval run --provider fake${selected.length ? ` ${selected.join(" ")}` : ""}`;
  };
  for (const input of [tags, caseIds, maxCases, baseline, saveBaseline]) {
    input.addEventListener("input", updatePreview);
    input.addEventListener("change", updatePreview);
  }
  updatePreview();

  form.append(
    h(
      "div",
      { class: "form-grid" },
      field("Tags", tags, "All listed tags must match."),
      field("Case IDs", caseIds, "Comma or space separated."),
      field("Max cases", maxCases, "Leave blank to use every match."),
      field("Baseline", baseline, "Used by relative gates and comparison."),
      field("Save as baseline", saveBaseline, "Overwrites a baseline with the same name."),
      field("Code revision", codeRevision),
    ),
    h("h3", { text: "Thresholds" }),
    h("p", { class: "small muted" }, "Blank clears a threshold. Values are validated by the CLI gate layer."),
    thresholdGrid,
    h(
      "div",
      { class: "button-row" },
      h("button", { class: "button primary", type: "submit" }, "Run fake replay"),
      h("span", { class: "small muted", text: "Only the fake provider can start here." }),
    ),
    h("h3", { text: "CLI equivalent" }),
    preview,
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    const payload = {
      provider: "fake",
      case_ids: caseIds.value.trim() ? caseIds.value.trim().split(/[,\s]+/).filter(Boolean) : [],
      tags: tags.value.trim() ? tags.value.trim().split(/[,\s]+/).filter(Boolean) : [],
      max_cases: maxCases.value ? Number(maxCases.value) : null,
      baseline: baseline.value || null,
      save_baseline: saveBaseline.value.trim() || null,
      code_revision: codeRevision.value.trim() || null,
      thresholds: {},
    };
    for (const [key, input] of thresholdInputs) {
      payload.thresholds[key] = input.value.trim() === "" ? null : Number(input.value);
    }
    try {
      const data = await api("/api/run", { method: "POST", body: payload });
      flash("Replay started.");
      location.hash = `#/runs?job=${encodeURIComponent(data.job.id)}`;
      if (location.hash.startsWith("#/runs?job=")) await render();
    } catch (error) {
      showFormError(form, error.message);
      submit.disabled = false;
    }
  });

  return form;
}

function startJobPolling(jobId, panel) {
  clearTimeout(jobTimer);
  const tick = async () => {
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      const job = data.job;
      updateJobPanel(panel, job);
      if (job.status === "completed") {
        flash(`Run complete: ${job.run_id}`);
        location.hash = `#/runs/${encodeURIComponent(job.run_id)}`;
        return;
      }
      if (job.status === "failed") {
        showToast(job.error || "Replay failed.", true);
        return;
      }
      jobTimer = setTimeout(tick, 700);
    } catch (error) {
      updateJobError(panel, error.message);
    }
  };
  tick();
}

function updateJobPanel(panel, job) {
  const total = Math.max(Number(job.total) || 1, 1);
  const done = Math.min(Number(job.done) || 0, total);
  panel.replaceChildren(
    h("h2", { text: "Replay job" }),
    h(
      "div",
      { class: "job" },
      h("progress", { max: String(total), value: String(done), "aria-label": "Replay progress" }),
      h(
        "div",
        { class: "job-meta" },
        h("span", { text: `${done} of ${total} cases` }),
        statusChip(job.status),
        h("span", { class: "mono small", text: job.id }),
      ),
      job.status === "running"
        ? h("p", { class: "small muted", text: "The server is replaying cases; this page polls the job status." })
        : null,
      job.run_id
        ? h("p", { class: "small" }, h("a", { href: `#/runs/${encodeURIComponent(job.run_id)}` }, "Open completed run"))
        : null,
      job.error ? banner("fail", "Replay failed", job.error) : null,
    ),
  );
}

function updateJobError(panel, message) {
  panel.replaceChildren(
    h("h2", { text: "Replay job" }),
    banner("fail", "Could not read job status", message),
    h("a", { class: "button", href: "#/runs" }, "Back to runs"),
  );
}

// ----------------------------------------------------------------- run view
async function runDetailView(runId, query) {
  const baselineParam = query.get("baseline");
  const suffix = baselineParam ? `?baseline=${encodeURIComponent(baselineParam)}` : "";
  const [data, baselinesData] = await Promise.all([
    api(`/api/runs/${encodeURIComponent(runId)}${suffix}`),
    api("/api/baselines"),
  ]);
  const run = data.run;
  const summary = run.summary || {};
  const gate = data.gate;
  const deltas = data.deltas || {};
  const baseline = data.baseline;
  const baselines = baselinesData.baselines || [];
  const comparison = data.comparison || [];
  const results = data.results || [];
  const clusters = data.clusters || [];

  const gateBanner = gate
    ? gate.passed
      ? banner("", "Gates passed", `${gate.gates.length} gate(s) evaluated. The CLI would exit 0.`)
      : banner("fail", "Gates failed", gate.gates.filter((item) => !item.passed).map((item) => item.detail).join(" · "))
    : banner("warn", "Gate not evaluated", "The run is incomplete or its stored summary is unavailable.");

  const baselineSelect = selectControl(
    "run-baseline-select",
    [["", "Run baseline"], ...baselines.map((item) => [item.name, item.name])],
    baselineParam || "",
  );
  baselineSelect.setAttribute("aria-label", "Compare against baseline");
  baselineSelect.addEventListener("change", () => {
    const name = baselineSelect.value;
    location.hash = `#/runs/${encodeURIComponent(runId)}${name ? `?baseline=${encodeURIComponent(name)}` : ""}`;
  });

  const comparisonRows = comparison.map((row) => ({
    dataset: {
      changed: row.changed ? "1" : "0",
      newcase: row.new_case ? "1" : "0",
      status: row.candidate ? row.candidate.status : "missing",
    },
    cells: [
      {
        node: h(
          "a",
          { href: `#/cases/${encodeURIComponent(row.case.case_id)}?run=${encodeURIComponent(run.run_id)}` },
          code(row.case.case_id),
        ),
      },
      { node: row.candidate ? statusChip(row.candidate.status) : h("span", { text: "—" }) },
      { node: row.baseline ? statusChip(row.baseline.status) : h("span", { class: "small muted", text: "not in baseline" }) },
      {
        node: row.divergence
          ? h("span", { class: "small", text: `${row.divergence.field}${row.divergence.line ? ` · line ${row.divergence.line}` : ""}` })
          : h("span", { class: "small muted", text: row.new_case ? "new case" : "no divergence" }),
      },
      { node: h("span", { class: "num", text: row.candidate ? fmtCost(row.candidate.cost_usd) : "—" }) },
      { node: h("span", { class: "num", text: row.candidate ? fmtMs(row.candidate.latency_ms) : "—" }) },
    ],
  }));

  const comparisonTable = dataTable(
    "Baseline comparison",
    [
      { label: "Case" },
      { label: "Candidate" },
      { label: "Baseline" },
      { label: "Divergence" },
      { label: "Cost", num: true },
      { label: "Latency", num: true },
    ],
    comparisonRows,
    "No comparison rows.",
  );
  const comparisonFilter = selectControl(
    "comparison-filter",
    [
      ["all", "All cases"],
      ["changed", "Changed only"],
      ["failing", "Candidate failures"],
      ["new", "New cases"],
    ],
    "all",
  );
  comparisonFilter.setAttribute("aria-label", "Filter comparison rows");
  comparisonFilter.addEventListener("change", () => {
    const value = comparisonFilter.value;
    for (const row of comparisonTable.querySelectorAll("tbody tr[data-changed]")) {
      const show =
        value === "all" ||
        (value === "changed" && row.dataset.changed === "1") ||
        (value === "failing" && row.dataset.status !== "pass") ||
        (value === "new" && row.dataset.newcase === "1");
      row.hidden = !show;
    }
  });

  const resultRows = results.map((result) => {
    const failure = firstFailure(result);
    return {
      cells: [
        {
          node: h(
            "a",
            { href: `#/cases/${encodeURIComponent(result.case_id)}?run=${encodeURIComponent(run.run_id)}` },
            code(result.case_id),
          ),
        },
        { node: statusChip(result.status) },
        { node: h("span", { class: "small", text: failure ? `${failure.name}: ${truncate(failure.message, 90)}` : "—" }) },
        { node: h("span", { class: "small", text: `${(result.assertions || []).filter((item) => item.status === "pass").length}/${(result.assertions || []).length}` }) },
        { node: h("span", { class: "num", text: fmtCost(result.cost_usd) }) },
        { node: h("span", { class: "num", text: fmtMs(result.latency_ms) }) },
      ],
    };
  });

  return h(
    "div",
    { class: "view" },
    h("p", { class: "small" }, h("a", { href: "#/runs" }, "← All runs")),
    pageHead(
      "Run",
      "Run detail",
      `${run.provider || "—"} / ${run.model || "—"} · ${fmtDate(run.started_at)}`,
      h(
        "div",
        { class: "button-row" },
        baselineSelect,
        h("a", { class: "button", href: "#/runs" }, "New run"),
      ),
    ),
    h(
      "div",
      { class: "button-row" },
      statusChip(run.status),
      h("span", { class: "mono small", text: run.run_id }),
      run.baseline_name ? h("span", { class: "chip neutral plain", text: `baseline ${run.baseline_name}` }) : null,
      run.code_revision ? h("span", { class: "small muted", text: `code ${run.code_revision}` }) : null,
      run.prompt_version ? h("span", { class: "small muted", text: `prompt ${run.prompt_version}` }) : null,
    ),
    gateBanner,
    h(
      "div",
      { class: "grid stats" },
      stat("Cases", fmtNumber(summary.total_cases), `${summary.passed || 0} passed · ${summary.failed || 0} failed`),
      stat("Pass rate", fmtPct(summary.pass_rate), baseline ? `Baseline ${fmtPoints(deltas.pass_rate)}` : "No baseline"),
      stat("Average cost", fmtCost(summary.avg_cost_usd), `Total ${fmtCost(summary.total_cost_usd)}`),
      stat("p95 latency", fmtMs(summary.p95_latency_ms), `Average ${fmtMs(summary.avg_latency_ms)}`),
    ),
    gate
      ? panel(
          "Gate detail",
          dataTable(
            "Gate results",
            [
              { label: "Gate" },
              { label: "Status" },
              { label: "Actual" },
              { label: "Limit" },
              { label: "Detail" },
            ],
            gate.gates.map((item) => ({
              cells: [
                { node: code(item.name) },
                { node: statusChip(item.passed ? "pass" : "fail") },
                { node: h("span", { class: "num", text: item.actual === null ? "—" : String(item.actual) }) },
                { node: h("span", { class: "num", text: item.limit === null ? "—" : String(item.limit) }) },
                { node: h("span", { class: "small", text: item.detail }) },
              ],
            })),
            "No gates configured.",
          ),
        )
      : null,
    baseline
      ? baselinePanel({ baseline, deltas, baseline_result: null, result: null })
      : panel("Baseline comparison", h("p", { class: "muted" }, "This run has no baseline.")),
    panel(
      "Failure clusters",
      clusters.length
        ? h(
            "div",
            { class: "cluster-list" },
            clusters.map((cluster) =>
              h(
                "div",
                { class: "cluster" },
                h(
                  "div",
                  {},
                  h("h3", { text: cluster.title }),
                  h("p", { text: `${cluster.case_ids.length} case(s) · ${cluster.example || ""}` }),
                ),
                h("span", { class: "count", text: String(cluster.count) }),
              ),
            ),
          )
        : h("p", { class: "muted" }, "No failures in this run."),
    ),
    baseline
      ? panelWithAction("Case comparison", comparisonFilter, comparisonTable)
      : null,
    panel("All results", dataTable("Run results", resultColumns(), resultRows, "No results.")),
    panel("Run configuration", jsonDetails("Show stored run config", run.config)),
  );
}

function resultColumns() {
  return [
    { label: "Case" },
    { label: "Status" },
    { label: "First failure" },
    { label: "Assertions" },
    { label: "Cost", num: true },
    { label: "Latency", num: true },
  ];
}

// ----------------------------------------------------------------- settings
// ----------------------------------------------------------------- CI setup
const CI_THRESHOLD_FIELDS = [
  ["min_pass_rate", "Minimum pass rate", "0–1 scale, e.g. 0.85", "rate", "e.g. 0.85"],
  ["max_total_cost_usd", "Max total cost (USD)", "Whole run", "money", "e.g. 0.50"],
  ["max_avg_cost_usd", "Max average cost (USD)", "Per case", "money", "e.g. 0.05"],
  ["max_p95_latency_ms", "Max p95 latency (ms)", "Whole run", "number", "e.g. 2000"],
  ["max_infrastructure_errors", "Max infrastructure errors", "Whole run", "integer", "e.g. 0"],
  ["max_signal_failures", "Max signal failures", "Whole run", "integer", "e.g. 0"],
  ["max_pass_rate_drop", "Max pass-rate drop", "0–1 scale vs baseline", "rate", "e.g. 0.05"],
  ["max_cost_increase_pct", "Max cost increase %", "vs baseline", "percent", "e.g. 20"],
  ["max_latency_increase_pct", "Max latency increase %", "vs baseline", "percent", "e.g. 20"],
];

function ciDraftQuery(baseline, thresholds) {
  const params = new URLSearchParams();
  if (baseline !== null) params.set("baseline", baseline);
  for (const [key] of CI_THRESHOLD_FIELDS) {
    const value = (thresholds[key] ?? "").toString().trim();
    if (value !== "") params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

function ciValidateField(kind, raw) {
  const value = raw.trim();
  if (value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return "Enter a number or leave the field blank.";
  if (number < 0) return "Use a value of zero or more.";
  if (kind === "integer" && !Number.isInteger(number)) return "Use a whole number.";
  if (kind === "rate" && number > 1) return "Rates use the 0–1 scale, for example 0.85.";
  return null;
}

async function copyToClipboard(value, label) {
  try {
    await navigator.clipboard.writeText(value);
    showToast(`${label} copied.`);
  } catch {
    showToast("Clipboard unavailable in this context; select the text and copy it manually.", true);
  }
}

function ciCopyButton(getValue, label) {
  const button = h("button", { class: "button small", type: "button", "aria-label": `Copy ${label}` }, "Copy");
  button.addEventListener("click", () => copyToClipboard(getValue(), label));
  return button;
}

async function ciView(query) {
  const initial = {};
  for (const [key] of CI_THRESHOLD_FIELDS) {
    initial[key] = query.get(key) || "";
  }
  let baseline = query.has("baseline") ? query.get("baseline") : null;
  const draft = { ...initial };

  const previews = {
    command: h("pre", { class: "code-block", id: "ci-command" }),
    config: h("pre", { class: "code-block", id: "ci-config" }),
    workflow: h("pre", { class: "code-block", id: "ci-workflow" }),
  };
  const status = h("p", { class: "small muted", role: "status", "aria-live": "polite", text: "Loading gates…" });
  const formError = h("p", { class: "form-error", role: "alert" });

  const baselineSelect = h("select", { id: "ci-baseline" });
  baselineSelect.append(h("option", { value: "", text: "No baseline (absolute gates only)" }));

  const inputs = new Map();
  const grid = h("div", { class: "form-grid three" });
  for (const [key, label, hint, kind, placeholder] of CI_THRESHOLD_FIELDS) {
    const input = h("input", {
      id: `ci-threshold-${key}`,
      type: "number",
      step: kind === "integer" ? "1" : "any",
      min: "0",
      max: kind === "rate" ? "1" : null,
      value: initial[key],
      placeholder,
      "aria-describedby": `ci-threshold-${key}-hint`,
    });
    inputs.set(key, { input, kind });
    grid.append(
      h(
        "div",
        { class: "field" },
        h("label", { for: input.id }, label),
        input,
        h("p", { class: "hint", id: `ci-threshold-${key}-hint`, text: hint }),
      ),
    );
  }

  let timer = null;

  const applyPayload = (data) => {
    previews.command.textContent = data.command;
    previews.config.textContent = data.config_toml;
    previews.workflow.textContent = data.workflow;
    baselineSelect.replaceChildren(h("option", { value: "", text: "No baseline (absolute gates only)" }));
    for (const name of data.baselines || []) {
      baselineSelect.append(h("option", { value: name, text: name }));
    }
    baselineSelect.value = data.baseline || "";
    baseline = baselineSelect.value;
    status.textContent = data.baseline
      ? `Gates compare against baseline ${data.baseline}.`
      : "No baseline selected; only absolute gates are evaluated.";
    formError.textContent = "";
  };

  const refresh = async () => {
    const problems = [];
    for (const [key, { input, kind }] of inputs) {
      const problem = ciValidateField(kind, input.value);
      input.setAttribute("aria-invalid", problem ? "true" : "false");
      if (problem) problems.push(`${key}: ${problem}`);
    }
    if (problems.length > 0) {
      formError.textContent = problems.join(" ");
      status.textContent = "Fix the gate values to refresh the preview.";
      return;
    }
    const suffix = ciDraftQuery(baseline, draft);
    history.replaceState(null, "", `#/ci${suffix}`);
    try {
      const data = await api(`/api/ci${suffix}`);
      applyPayload(data);
    } catch (error) {
      formError.textContent = error.message;
      status.textContent = "The preview could not be generated.";
    }
  };

  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  };

  for (const [key, { input }] of inputs) {
    input.addEventListener("input", () => {
      draft[key] = input.value;
      schedule();
    });
  }
  baselineSelect.addEventListener("change", () => {
    baseline = baselineSelect.value;
    schedule();
  });

  const runButton = h("button", { class: "button primary", type: "button" }, "Run locally");
  runButton.addEventListener("click", async () => {
    runButton.disabled = true;
    const thresholds = {};
    for (const [key, { input }] of inputs) {
      thresholds[key] = input.value.trim() === "" ? null : Number(input.value);
    }
    try {
      const data = await api("/api/run", {
        method: "POST",
        body: { provider: "fake", baseline: baseline || null, thresholds },
      });
      flash("Replay started with these gates.");
      location.hash = `#/runs?job=${encodeURIComponent(data.job.id)}`;
      if (location.hash.startsWith("#/runs?job=")) await render();
    } catch (error) {
      formError.textContent = error.message;
      runButton.disabled = false;
    }
  });

  const view = h(
    "div",
    { class: "view" },
    pageHead(
      "CI",
      "CI setup",
      "Copy the workflow and config, or replay the same gates locally before you commit them.",
    ),
    banner(
      "",
      "Deterministic gates only",
      "Exit codes come from assertions, thresholds, and budgets. No model decides whether the build passes.",
    ),
    panel(
      "Gates",
      h(
        "div",
        { class: "form-grid" },
        h(
          "div",
          { class: "field" },
          h("label", { for: "ci-baseline" }, "Baseline"),
          baselineSelect,
          h("p", { class: "hint", text: "Relative gates need a saved baseline; without one the CLI fails closed." }),
        ),
      ),
      h("p", { class: "small muted" }, "Blank clears a threshold. Values are validated by the same gate layer the CLI uses."),
      grid,
      status,
      h(
        "div",
        { class: "button-row" },
        runButton,
        h("span", { class: "small muted", text: "Replays the stored cases with the fake provider and these gates." }),
      ),
      formError,
    ),
    panelWithAction(
      "Run command",
      ciCopyButton(() => previews.command.textContent, "run command"),
      previews.command,
      h("p", { class: "small muted", text: "Run it from the repository root after importing traces." }),
    ),
    panelWithAction(
      "Config file",
      ciCopyButton(() => previews.config.textContent, "config file"),
      h("p", { class: "small muted" }, "Save as trace-to-eval.toml, or paste the tables under [tool.trace_to_eval] in pyproject.toml."),
      previews.config,
    ),
    panelWithAction(
      "GitHub Actions workflow",
      ciCopyButton(() => previews.workflow.textContent, "workflow"),
      h("p", { class: "small muted" }, "Save as .github/workflows/trace-to-eval.yml. The job fails on a non-zero exit code."),
      previews.workflow,
    ),
    panel(
      "Artifacts and retention",
      h("p", { id: "ci-retention", text: "" }),
      h("p", { class: "small muted", id: "ci-live", text: "" }),
    ),
  );

  try {
    const data = await api(`/api/ci${ciDraftQuery(baseline, draft)}`);
    applyPayload(data);
    view.querySelector("#ci-retention").textContent = data.retention_note;
    view.querySelector("#ci-live").textContent = data.live_note;
  } catch (error) {
    formError.textContent = error.message;
    status.textContent = "The preview could not be generated.";
  }
  return view;
}

async function settingsView() {
  const settings = await api("/api/settings");
  const thresholds = settings.thresholds || {};
  const budget = settings.budget || {};
  const redaction = settings.redaction || {};
  const counts = settings.counts || {};
  const adapters = settings.adapters || [];
  return h(
    "div",
    { class: "view" },
    pageHead(
      "Settings",
      "Settings",
      "Read-only view of the resolved configuration. The store and reports stay on this machine.",
    ),
    banner(
      "",
      "Local-first by design",
      "No accounts, telemetry, or uploads. Cases are stored in a local SQLite file; deleting a case removes its labels and stored results.",
    ),
    h(
      "div",
      { class: "grid stats" },
      stat("Cases", fmtNumber(counts.cases), "In the local store"),
      stat("Runs", fmtNumber(counts.runs), "Stored summaries"),
      stat("Provider", settings.provider || "—", "Default for CLI runs"),
      stat("Model", settings.model || "—", "Configured model"),
    ),
    panel(
      "Provider adapters",
      h(
        "div",
        { class: "grid" },
        adapters.map((adapter) =>
          h(
            "div",
            { class: "adapter" },
            h(
              "h3",
              {},
              h("span", { text: adapter.name }),
              h("span", { class: `chip ${adapter.status === "enabled" ? "pass" : "neutral"} plain`, text: adapter.status }),
            ),
            h("p", { text: adapter.detail }),
          ),
        ),
      ),
      h("p", { class: "small muted" }, settings.live ? settings.live.note : ""),
      settings.live && settings.live.env_consent
        ? banner("warn", "Live consent variable detected", "The UI still refuses to start live providers; use the CLI with explicit caps.")
        : null,
    ),
    h(
      "div",
      { class: "split" },
      panel(
        "Thresholds",
        dataTable(
          "Configured thresholds",
          [{ label: "Threshold" }, { label: "Value", num: true }],
          Object.entries(thresholds).map(([key, value]) => ({
            cells: [{ node: code(key) }, { node: h("span", { class: "num", text: value === null ? "—" : String(value) }) }],
          })),
        ),
      ),
      panel(
        "Budget and retention",
        metricCells([
          ["Max requests", budget.max_requests ?? "—"],
          ["Max run cost", fmtCost(budget.max_run_cost_usd)],
          ["Daily cost", fmtCost(budget.daily_cost_usd)],
          ["Monthly cost", fmtCost(budget.monthly_cost_usd)],
          ["Request timeout", `${budget.request_timeout_s ?? "—"} s`],
          ["Database", code(settings.db)],
        ]),
        h("p", { class: "small muted", text: settings.retention || "" }),
      ),
    ),
    panel(
      "Redaction",
      metricCells([
        ["Enabled", (redaction.enabled || []).join(", ") || "—"],
        ["Disabled", (redaction.disabled || []).join(", ") || "—"],
        ["Custom patterns", (redaction.custom || []).join(", ") || "None"],
        ["Applied", "Before persistence, at import time"],
      ]),
      h("p", { class: "small muted" }, "Redaction is configured in the CLI config file and is intentionally not editable from the UI."),
    ),
    panel(
      "Configuration source",
      metricCells([
        ["Source", settings.config_source || "defaults"],
        ["Fake script", settings.fake_script || "Echo/mutate defaults"],
        ["Rubric", settings.rubric ? "Enabled" : "Disabled"],
        ["Rubric model", settings.rubric_model || "—"],
        ["Base URL", settings.base_url || "—"],
        ["Version", settings.version || "—"],
      ]),
    ),
  );
}

// -------------------------------------------------------------------- start
const skipLink = document.querySelector(".skip-link");
if (skipLink) {
  skipLink.addEventListener("click", (event) => {
    event.preventDefault();
    main.focus();
  });
}

window.addEventListener("hashchange", () => {
  render().catch((error) => showToast(error.message, true));
});
render({ focusHeading: false }).catch((error) => {
  main.replaceChildren(errorView(error));
});

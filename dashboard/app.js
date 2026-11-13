// Shell: view tabs, URL hash, polling every 30 s, header chain status and data age.

import * as coast from "./coast.js";
import * as turb from "./turb.js";
import { getJSON, fmtAge, fmtInt, esc, zoneName } from "./seal.js";

const POLL_MS = 30_000;
const STALE_MS = 45 * 60_000;
const VIEWS = { coast, turb };
const TASK_LABEL = { coast: "Coast", turb: "Turbulence" };

const app = {
  view: "coast",
  modules: {},
  states: { coast: undefined, turb: undefined },
  ledgers: { coast: null, turb: null },
  fetchedAt: { coast: 0, turb: 0 },
  failed: { coast: false, turb: false },
};

const header = document.querySelector("header.top");
const chainEl = header.querySelector(".chain");
const ageEl = header.querySelector(".age");

// ------------------------------------------------------------------ hash

function readHash() {
  const parts = decodeURIComponent(location.hash.replace(/^#/, "")).split("/").filter(Boolean);
  const view = VIEWS[parts[0]] ? parts[0] : "coast";
  return { view, parts: parts.slice(1) };
}

let writingHash = false;
function setHash(view, parts) {
  const h = "#" + [view, ...parts.filter((p) => p != null && p !== "")].map(encodeURIComponent).join("/");
  if (location.hash !== h) {
    writingHash = true;
    history.replaceState(null, "", h);
    writingHash = false;
  }
}

// ------------------------------------------------------------------ header

function renderHeader() {
  const v = app.view;
  const led = app.ledgers[v];
  const st = app.states[v];
  let records, graded, ok, message;
  if (led) {
    ({ records, graded } = led);
    ok = led.verified;
    message = led.message;
  } else if (st && !st.empty && st.ledger) {
    records = st.ledger.records;
    graded = st.ledger.graded;
    ok = st.ledger.verify ? st.ledger.verify.ok : null;
    message = st.ledger.verify ? st.ledger.verify.message : "";
  }
  header.classList.toggle("broken", ok === false);
  if (records == null) {
    chainEl.textContent = app.failed[v] ? "Cannot reach the lab server" : "Reading the ledger…";
  } else if (ok === false) {
    chainEl.innerHTML = `Chain check failed: ${esc(message)}. ${esc(fmtInt(records))} records.`;
  } else {
    const verdict = ok ? "chain verified" : "chain not yet checked";
    chainEl.textContent = `${fmtInt(records)} forecasts sealed, ${fmtInt(graded)} graded, ${verdict}`;
  }
  renderAge();
}

function renderAge() {
  const st = app.states[app.view];
  ageEl.classList.remove("stale");
  if (st === undefined) {
    ageEl.textContent = "";
    return;
  }
  if (!st || st.empty) {
    ageEl.textContent = app.failed[app.view] ? "no data from the server" : "no live data yet";
    ageEl.classList.toggle("stale", !!app.failed[app.view]);
    return;
  }
  const age = Date.now() - new Date(st.generated_at).getTime();
  const tz = zoneName();
  ageEl.textContent = `updated ${fmtAge(age)}${tz ? ` · times ${tz}` : ""}`;
  if (age > STALE_MS) {
    ageEl.classList.add("stale");
    ageEl.textContent = `updated ${fmtAge(age)}: the live job may have stopped`;
  }
}

// ------------------------------------------------------------------ polling

async function fetchState(task) {
  const s = await getJSON(`/api/${task}/state`);
  if (s) {
    app.states[task] = s;
    app.failed[task] = false;
  } else if (app.states[task] === undefined) {
    app.states[task] = null;
    app.failed[task] = true;
  } else {
    app.failed[task] = true; // keep the last good state on screen
  }
  app.fetchedAt[task] = Date.now();
  app.modules[task].setState(app.states[task], { failed: app.failed[task] });
}

async function fetchLedger(task) {
  const led = await getJSON(`/api/${task}/ledger?tail=1`);
  if (led) app.ledgers[task] = led;
}

let polling = null;
async function poll() {
  const v = app.view;
  const other = v === "coast" ? "turb" : "coast";
  await Promise.all([fetchState(v), fetchLedger(v)]);
  renderHeader();
  await app.modules[v].poll();
  // keep the other view warm so switching is instant
  fetchState(other);
}

function schedule() {
  clearInterval(polling);
  polling = setInterval(poll, POLL_MS);
}

// ------------------------------------------------------------------ views

function show(view, parts) {
  app.view = view;
  for (const [name, mod] of Object.entries(app.modules)) {
    const on = name === view;
    document.getElementById(`view-${name}`).hidden = !on;
    const tab = document.getElementById(`tab-${name}`);
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.tabIndex = on ? 0 : -1;
    if (!on) mod.deactivate();
  }
  document.title = `foresight-lab · ${TASK_LABEL[view]}`;
  app.modules[view].activate(parts || []);
  renderHeader();
}

function init() {
  for (const [name, mod] of Object.entries(VIEWS)) {
    app.modules[name] = mod.create(document.getElementById(`view-${name}`), {
      getJSON,
      setHash: (parts) => setHash(name, parts),
    });
  }

  const tabs = [...document.querySelectorAll(".tabs button")];
  tabs.forEach((b, i) => {
    b.addEventListener("click", () => {
      if (b.dataset.view === app.view) return;
      show(b.dataset.view, []);
      setHash(b.dataset.view, app.modules[b.dataset.view].hashParts());
      poll();
    });
    b.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        next.focus();
        next.click();
      }
    });
  });

  window.addEventListener("hashchange", () => {
    if (writingHash) return;
    const { view, parts } = readHash();
    show(view, parts);
    poll();
  });

  let rt = null;
  window.addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => app.modules[app.view].resize(), 150);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && Date.now() - app.fetchedAt[app.view] > POLL_MS) poll();
  });

  const { view, parts } = readHash();
  show(view, parts);
  poll();
  schedule();
  setInterval(renderAge, 15_000);
}

init();

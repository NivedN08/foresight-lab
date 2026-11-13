// The seal mark, shared by both views, plus small lettering helpers.
//
// A sealed forecast is an open magenta diamond. When the truth arrives the
// diamond opens: filled magenta if the forecast was good, a --miss outline with
// a short strike if it missed. The change is a CSS transition on an element
// that already exists, so it plays once, only when a grade lands while the
// page is open, and never under prefers-reduced-motion (see styles.css).

const d3 = window.d3;

export function diamondPath(r) {
  return `M0,${-r}L${r},0L0,${r}L${-r},0Z`;
}

// states: "open" (sealed, waiting), "good", "miss", "neutral" (graded, nothing to judge)
export function sealClass(state) {
  return `seal seal-${state || "open"}`;
}

// Join seal marks into `parent` (a d3 selection of a <g>).
// items: [{key, x, y, state, r?}]; returns the merged selection for extra wiring.
export function drawSeals(parent, items, { r = 5 } = {}) {
  return parent.selectAll("g.seal")
    .data(items, (d) => d.key)
    .join(
      (enter) => {
        const g = enter.append("g");
        g.append("path").attr("class", "seal-body");
        g.append("line").attr("class", "seal-strike");
        return g;
      },
      (update) => update,
      (exit) => exit.remove(),
    )
    .attr("class", (d) => sealClass(d.state))
    .attr("transform", (d) => `translate(${d.x},${d.y})`)
    .each(function (d) {
      const rr = d.r || r;
      const g = d3.select(this);
      g.select("path.seal-body").attr("d", diamondPath(rr));
      const s = rr * 1.25;
      g.select("line.seal-strike")
        .attr("x1", -s).attr("y1", s * 0.55).attr("x2", s).attr("y2", -s * 0.55)
        .attr("pathLength", 1);
    });
}

// Thin magenta tick back to the moment of sealing: a hairline from x0 to x1 at y
// with a short upright stop at x0.
export function drawTicks(parent, items) {
  return parent.selectAll("path.seal-tick")
    .data(items, (d) => d.key)
    .join("path")
    .attr("class", "seal-tick")
    .attr("d", (d) => `M${d.x0},${d.y - 3}V${d.y + 3}M${d.x0},${d.y}H${d.x1}`);
}

// A standalone inline SVG of one seal mark, for legends and the log row.
export function sealSVG(state, r = 5) {
  const w = r * 2 + 6;
  const s = r * 1.25;
  return `<svg class="seal-inline" width="${w}" height="${w}" viewBox="${-w / 2} ${-w / 2} ${w} ${w}" aria-hidden="true">` +
    `<g class="${sealClass(state)}"><path class="seal-body" d="${diamondPath(r)}"/>` +
    `<line class="seal-strike" pathLength="1" x1="${-s}" y1="${s * 0.55}" x2="${s}" y2="${-s * 0.55}"/></g></svg>`;
}

// ---------------------------------------------------------------- time

export const parseTime = (s) => (s ? new Date(s) : null);

const hm = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
const dayHm = new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });

export function fmtTime(t) {
  if (!t) return "";
  const d = t instanceof Date ? t : new Date(t);
  return hm.format(d);
}

// Time with the weekday when it is not today.
export function fmtWhen(t) {
  if (!t) return "";
  const d = t instanceof Date ? t : new Date(t);
  const now = new Date();
  return d.toDateString() === now.toDateString() ? hm.format(d) : dayHm.format(d);
}

export function zoneName() {
  try {
    const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).formatToParts(new Date());
    return (parts.find((p) => p.type === "timeZoneName") || {}).value || "";
  } catch {
    return "";
  }
}

export function fmtAge(ms) {
  const min = Math.round(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} h ${m} min ago` : `${h} h ago`;
}

export const fmtInt = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));

const WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"];
export const numberWord = (n) => (Number.isInteger(n) && n >= 0 && n < WORDS.length ? WORDS[n] : String(n));
export const capital = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------------------------------------------------------- tooltip

let tipEl = null;
function tip() {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "tip";
    tipEl.setAttribute("role", "tooltip");
    document.body.appendChild(tipEl);
  }
  return tipEl;
}

export function showTip(html, event) {
  const el = tip();
  el.innerHTML = html;
  el.style.display = "block";
  const pad = 14;
  const { innerWidth: W, innerHeight: H } = window;
  const r = el.getBoundingClientRect();
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + r.width > W - 8) x = event.clientX - r.width - pad;
  if (y + r.height > H - 8) y = event.clientY - r.height - pad;
  el.style.left = `${Math.max(8, x)}px`;
  el.style.top = `${Math.max(8, y)}px`;
}

export function hideTip() {
  if (tipEl) tipEl.style.display = "none";
}

// ---------------------------------------------------------------- misc

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Width of an element's content box, at least `min`.
export function boxSize(el, minW = 200, minH = 120) {
  const r = el.getBoundingClientRect();
  return { w: Math.max(minW, Math.floor(r.width)), h: Math.max(minH, Math.floor(r.height)) };
}

// Fetch JSON; null on any failure (the caller keeps what it had).
export async function getJSON(url) {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

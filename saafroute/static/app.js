"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KIND = { overflowing_bin: "Overflowing bin", illegal_dump: "Illegal dumping", litter: "Litter", clean: "No waste" };
const WASTE = { mixed: "Mixed", dry_recyclable: "Dry / recyclable", wet_organic: "Wet / organic", construction_debris: "Construction debris",
  bulky: "Bulky items", e_waste: "E-waste", hazardous: "Hazardous", none: "None" };
const TRUCK_COLORS = ["#1565c0", "#00897b", "#8e24aa", "#f4511e", "#3949ab"];
const COLORS = { high: "#c0381b", mid: "#b86b00", low: "#2a7a4b", pend: "#5b4fd1", none: "#6b7280" };

let reports = [];
let filter = "pending_review";
let expandedId = null;
let map, reportLayer, routeLayer, baseLayer, lastPlan = null;
const markers = new Map();
let sectorList = [], gpsFix = null;

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error */ }
  if (!res.ok) throw new Error((body && body.detail && (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail))) || `Request failed (${res.status})`);
  return body;
}

function scoreHex(r) {
  if (r.status === "pending_review") return COLORS.pend;
  if (r.score == null) return COLORS.none;
  return r.score >= 70 ? COLORS.high : r.score >= 40 ? COLORS.mid : COLORS.low;
}

/* ---------- status ---------- */
async function loadStatus() {
  try {
    const h = await api("/api/health");
    const v = h.vision, d = h.drafting;
    $("#status").innerHTML =
      `<span class="chip ${v.available ? "ok" : "bad"}">Vision · ${esc(v.model.replace("gemini-api:", ""))}</span>` +
      `<span class="chip ${d.model !== "template" ? "ok" : "bad"}">Drafting · ${esc(d.model.replace("gemini-api:", ""))}</span>`;
  } catch {
    $("#status").innerHTML = `<span class="chip bad">Server unreachable</span>`;
  }
}

/* ---------- summary tiles ---------- */
function updateStats() {
  const open = reports.filter((r) => r.kind && r.kind !== "clean");
  $("#stat-review").textContent = open.filter((r) => r.status === "pending_review").length;
  const approved = open.filter((r) => r.status === "approved").length;
  $("#stat-approved").textContent = approved;
  const p = lastPlan;
  if (p && p.stops) {
    $("#stat-approved-sub").textContent = `on ${p.routes.filter((r) => r.stop_ids.length).length} trucks`;
    $("#stat-km").textContent = `${p.optimised_km} km`;
    $("#stat-km-sub").textContent = `vs ${p.baseline_km} fixed`;
    $("#stat-saved").textContent = `${p.saved_pct}%`;
    $("#stat-saved-sub").textContent = `${p.saved_km} km shorter`;
  } else {
    $("#stat-approved-sub").textContent = "tickets";
    $("#stat-km").textContent = "–";
    $("#stat-km-sub").textContent = "approve tickets to plan";
    $("#stat-saved").textContent = "–";
    $("#stat-saved-sub").textContent = "vs fixed route";
  }
}

/* ---------- map ---------- */
async function initMap() {
  map = L.map("map", { zoomSnap: 0.25 }).setView([30.728, 76.775], 12.75);
  const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: "© OpenStreetMap contributors",
  }).addTo(map);
  // Our own sector grid is drawn only when map tiles fail (offline venue), so it never clutters OSM labels.
  const grid = L.layerGroup();
  let tileErrors = 0, tileLoads = 0;
  tiles.on("tileload", () => { tileLoads++; });
  tiles.on("tileerror", () => {
    if (++tileErrors === 3 && tileLoads === 0) {
      grid.addTo(map);
      $("#map").title = "Offline: map tiles unavailable, showing sector grid only";
    }
  });

  const { sectors, depot } = await api("/api/sectors");
  sectorList = sectors;
  const sel = $("#sector");
  sel.innerHTML = `<option value="" disabled selected>Choose sector…</option>` +
    sectors.map((s) => `<option value="${s.sector}">Sector ${s.sector}</option>`).join("");
  try { const last = localStorage.getItem("sector"); if (last) sel.value = last; } catch { /* storage blocked */ }
  for (const s of sectors) {
    L.circleMarker([s.lat, s.lon], { radius: 2, color: "#55635b", weight: 1, fillOpacity: .6 }).addTo(grid);
    L.marker([s.lat, s.lon], { icon: L.divIcon({ className: "sector-label", html: s.sector, iconSize: [24, 14], iconAnchor: [-4, 7] }), interactive: false }).addTo(grid);
  }
  L.marker([depot.lat, depot.lon], {
    icon: L.divIcon({ className: "", html: `<div style="background:#10291f;color:#fff;border:3px solid #fff;border-radius:10px;padding:2px 10px;font-size:13px;font-weight:700;white-space:nowrap">DEPOT</div>`, iconSize: [70, 26], iconAnchor: [35, 13] }),
  }).bindPopup(esc(depot.name)).addTo(map);
  reportLayer = L.layerGroup().addTo(map);
  routeLayer = L.layerGroup().addTo(map);
  baseLayer = L.layerGroup();
}

function pinIcon(r, selected) {
  const size = Math.round(28 + Math.min(r.score || 20, 100) / 8);
  const label = r.score != null ? Math.round(r.score) : "?";
  return L.divIcon({
    className: "",
    html: `<div class="pin${selected ? " sel" : ""}" style="width:${size}px;height:${size}px;background:${scoreHex(r)};font-size:${size > 36 ? 14 : 12}px">${label}</div>`,
    iconSize: [size, size], iconAnchor: [size / 2, size / 2],
  });
}

function drawReports() {
  reportLayer.clearLayers();
  markers.clear();
  for (const r of reports) {
    if (r.status === "rejected" || r.status === "collected" || r.status === "merged" || r.kind === "clean" || !r.kind) continue;
    const m = L.marker([r.lat, r.lon], { icon: pinIcon(r, r.id === expandedId), keyboard: false })
      .bindTooltip(`#${r.id} · ${KIND[r.kind] || "?"} · score ${r.score ?? "–"}${r.synthetic ? " · synthetic" : ""}`)
      .on("click", () => focusTicket(r.id));
    m.addTo(reportLayer);
    markers.set(r.id, m);
  }
}

/* ---------- tickets ---------- */
function breakdownHtml(b) {
  if (!b) return "";
  const parts = [["sev", b.severity], ["age", b.age], ["rep", b.repeats], ["site", b.site]];
  return `<details class="breakdown">
    <summary>Why this score? <b>${b.total}</b> of 100</summary>
    <div class="bar" aria-hidden="true">${parts.map(([k, v]) => `<i class="b-${k}" style="width:${v}%"></i>`).join("")}</div>
    Severity ${b.severity} + age ${b.age} + repeats ${b.repeats} (${b.repeat_count} nearby) + site ${b.site} (${esc(b.site_reason)}) = <b>${b.total}</b>
  </details>`;
}

function statusPill(r) {
  return r.status === "pending_review" ? `<span class="pill review">Review</span>` : `<span class="pill">${esc(r.status.replace("_", " "))}</span>`;
}

function compactHtml(r) {
  const needs = r.vision_status !== "ok";
  const title = r.kind ? `${esc(KIND[r.kind])} · Sector ${r.sector}` : `Sector ${r.sector} · needs a human look`;
  const sub = r.kind
    ? `${esc(WASTE[r.waste_type] || r.waste_type)} · severity ${r.severity}${r.report_count > 1 ? ` · ${r.report_count} reports` : ""}${r.synthetic ? " · synthetic" : ""}`
    : "The model's answer could not be validated";
  const box = needs
    ? `<div class="score" style="background:#e4def9;color:#3a2fa3">?</div>`
    : `<div class="score" style="background:${scoreHex(r)}">${r.score != null ? Math.round(r.score) : "–"}</div>`;
  return `<button type="button" class="ticket compact${needs ? " review-needed" : ""}" id="t-${r.id}" data-id="${r.id}" data-expand aria-label="Open ticket ${r.id}">
    ${box}${r.has_image ? `<img class="cthumb" loading="lazy" src="/api/reports/${r.id}/image" alt="">` : ""}<div class="c-main"><div class="ttitle">${title}</div><div class="meta">${sub}</div></div>${statusPill(r)}
  </button>`;
}

function ticketHtml(r) {
  const pending = r.status === "pending_review";
  const review = r.vision_status !== "ok";
  const pills = [
    r.synthetic ? `<span class="pill synth">synthetic</span>` : "",
    review ? `<span class="pill review">needs human review</span>` : "",
    r.image_blurred ? `<span class="pill blur">face/plate flagged</span>` : "",
    r.report_count > 1 ? `<span class="pill count" title="Display only: this does not change the score">${r.report_count} reports of this spot</span>` : "",
    pending ? "" : statusPill(r),
  ].join("");
  const lang = r.ticket_lang || "en";
  const scoreBox = `<div class="score" style="background:${scoreHex(r)}">${r.score != null ? Math.round(r.score) : "–"}<small>score</small></div>`;
  const photo = r.has_image
    ? `<img class="photo" data-zoom="${r.id}" loading="lazy" src="/api/reports/${r.id}/image" alt="Reported photo #${r.id} (tap to enlarge)" title="Tap to enlarge">`
    : `<div class="nophoto">${r.synthetic ? "Synthetic demo ticket: no photo" : "No photo for this ticket"}</div>`;
  const facts = r.kind
    ? `${esc(WASTE[r.waste_type] || r.waste_type)} · severity ${r.severity} of 5 · confidence ${Math.round((r.confidence || 0) * 100)}%`
    : `Model could not analyse this photo${r.vision_error ? `: ${esc(r.vision_error.slice(0, 120))}` : ""}`;
  const draft = pending && r.ticket_title
    ? `<div class="draft"><div class="draft-label">Draft ticket · edit before approving</div>
        <input class="t-title" value="${esc(r.ticket_title)}" maxlength="120" aria-label="Ticket title">
        <textarea class="t-body" maxlength="1200" aria-label="Ticket text">${esc(r.ticket_body)}</textarea></div>`
    : r.ticket_title ? `<div class="draft"><div class="ttitle">${esc(r.ticket_title)}</div><div class="body">${esc(r.ticket_body)}</div></div>` : "";
  const manual = pending && (!r.kind || r.kind === "clean")
    ? `<div class="actions manual"><span class="meta">Set the analysis manually:</span>
        <select class="m-kind" aria-label="Kind">${Object.entries(KIND).filter(([k]) => k !== "clean").map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
        <select class="m-waste" aria-label="Material">${Object.entries(WASTE).filter(([k]) => k !== "none").map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
        <select class="m-sev" aria-label="Severity">${[1, 2, 3, 4, 5].map((n) => `<option value="${n}"${n === 3 ? " selected" : ""}>Severity ${n}</option>`).join("")}</select>
        <button type="button" class="primary" data-act="analysis">Set analysis</button></div>` : "";
  const sectorFix = sectorList.length
    ? `<select data-act="sector" aria-label="Correct the sector" title="Wrong sector? Pick the right one">
        ${sectorList.map((s) => `<option value="${s.sector}"${s.sector === r.sector ? " selected" : ""}>Sector ${s.sector}</option>`).join("")}</select>` : "";
  const mismatch = r.sector_mismatch && r.nearest_sector
    ? `<div class="mismatch" role="alert">⚠ Sector ${r.sector} was chosen, but GPS places it near sector ${r.nearest_sector}.
        <button type="button" data-act="usesector" data-sector="${r.nearest_sector}">Use sector ${r.nearest_sector}</button></div>` : "";
  const actions = pending ? `<div class="actions">
      ${r.ticket_title ? `<button type="button" class="primary" data-act="approve">Approve</button>` : ""}
      <button type="button" data-act="reject">Reject</button>
      ${sectorFix}
      ${r.kind && r.kind !== "clean" ? `<select data-act="lang" aria-label="Ticket language (redrafts the ticket)" title="Ticket language: changing it redrafts the ticket">
        <option value="en"${lang === "en" ? " selected" : ""}>English</option><option value="hi"${lang === "hi" ? " selected" : ""}>हिन्दी</option><option value="pa"${lang === "pa" ? " selected" : ""}>ਪੰਜਾਬੀ</option></select>` : ""}
    </div>` : r.status === "approved" ? `<div class="actions"><button type="button" data-act="collected">Mark collected</button>${sectorFix}</div>` : "";
  return `<article class="ticket open" id="t-${r.id}" data-id="${r.id}">
    <div class="thead">${scoreBox}<div class="c-main">
      <div class="ttitle">${r.kind ? esc(KIND[r.kind]) : "Needs a human look"} · Sector ${r.sector}</div>
      <div class="meta">${facts}</div>
      ${pills ? `<div class="meta">${pills}</div>` : ""}
    </div></div>
    ${photo}
    ${mismatch}
    ${r.report_count > 1 ? `<div class="meta">Also reported by ${r.report_count - 1} other${r.report_count > 2 ? "s" : ""} at this spot.</div>` : ""}
    ${r.reason ? `<div class="reason">“${esc(r.reason)}”</div>` : ""}
    ${breakdownHtml(r.score_breakdown)}
    ${draft}
    ${manual}
    ${actions}
    <details class="tech"><summary>Details</summary>
      <div class="meta">#${r.id} · Vision: ${esc((r.vision_model || "–").replace("gemini-api:", ""))}${r.ticket_model ? ` · Draft: ${esc(r.ticket_model.replace("gemini-api:", ""))}` : ""}${r.reviewed_by ? ` · Reviewed by ${esc(r.reviewed_by)}` : ""}</div>
    </details>
  </article>`;
}

function visibleList() {
  return reports.filter((r) => r.status !== "merged" && (filter === "all" || r.status === filter)); // merged reports only raise a counter
}

function drawTickets() {
  const list = visibleList();
  if (!list.some((r) => r.id === expandedId)) expandedId = list.length ? list[0].id : null;
  $("#tickets").innerHTML = list.length
    ? list.map((r) => (r.id === expandedId ? ticketHtml(r) : compactHtml(r))).join("")
    : `<div class="empty">${filter === "pending_review" ? "No tickets waiting for review. New citizen reports will appear here." :
        filter === "approved" ? "No approved tickets. Approve tickets to plan routes." : "No reports yet."}</div>`;
  if (reportLayer) markers.forEach((m, id) => { const r = reports.find((x) => x.id === id); if (r) m.setIcon(pinIcon(r, id === expandedId)); });
}

async function refresh() {
  try {
    reports = await api("/api/reports");
    drawReports();
    drawTickets();
    updateStats();
  } catch (e) {
    $("#tickets").innerHTML = `<div class="empty">Could not load tickets: ${esc(e.message)}</div>`;
  }
}

function flyToTicket(id) {
  const m = map && markers.get(id);
  if (!m) return;
  const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const target = m.getLatLng(), zoom = Math.max(map.getZoom(), 15);
  if (reduce) map.setView(target, zoom, { animate: false });
  else map.flyTo(target, zoom, { duration: 1.1 });
  map.once("moveend", () => { if (expandedId === id && markers.get(id) === m) m.openTooltip(); });
}

function focusTicket(id, scroll = true) {
  const r = reports.find((x) => x.id === id);
  if (!r) return;
  if (filter !== "all" && r.status !== filter) { filter = "all"; document.querySelectorAll(".filters button").forEach((b) => b.classList.toggle("on", b.dataset.f === "all")); }
  expandedId = id;
  drawTickets();
  flyToTicket(id);
  const el = $(`#t-${id}`);
  if (!el) return;
  el.classList.add("focus");
  if (scroll) el.scrollIntoView({ behavior: "smooth", block: "center" });
}

function setFilter(f) {
  filter = f;
  document.querySelectorAll(".filters button").forEach((b) => b.classList.toggle("on", b.dataset.f === f));
  drawTickets();
}

function official() {
  const v = $("#official").value.trim();
  if (v.length < 2) { $("#official").focus(); throw new Error("Enter the reviewing official's name first."); }
  try { localStorage.setItem("official", v); } catch { /* storage blocked */ }
  return v;
}

function closeZoom() { const z = $("#zoom"); if (z) z.remove(); }
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeZoom(); });

$("#tickets").addEventListener("click", async (ev) => {
  const zoom = ev.target.closest("[data-zoom]");
  if (zoom) {
    closeZoom();
    const z = document.createElement("div");
    z.id = "zoom";
    z.setAttribute("role", "dialog");
    z.setAttribute("aria-label", "Photo, tap to close");
    z.innerHTML = `<img src="/api/reports/${Number(zoom.dataset.zoom)}/image" alt="Reported photo, enlarged">`;
    z.addEventListener("click", closeZoom);
    document.body.appendChild(z);
    return;
  }
  const open = ev.target.closest("[data-expand]");
  if (open) { focusTicket(Number(open.dataset.id), false); return; }
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const card = btn.closest(".ticket"), id = card.dataset.id, act = btn.dataset.act;
  btn.disabled = true;
  try {
    if (act === "approve") {
      await api(`/api/reports/${id}/approve`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ official: official(), title: $(".t-title", card)?.value, body: $(".t-body", card)?.value }) });
    } else if (act === "analysis") {
      await api(`/api/reports/${id}/analysis`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ official: official(), kind: $(".m-kind", card).value, waste_type: $(".m-waste", card).value,
          severity: Number($(".m-sev", card).value) }) });
      expandedId = Number(id);
      await refresh();
      await plan();
      return;
    } else if (act === "usesector") {
      await changeSector(id, Number(btn.dataset.sector));
      return;
    } else if (act === "reject") {
      await api(`/api/reports/${id}/reject`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ official: official() }) });
    } else if (act === "collected") {
      await api(`/api/reports/${id}/collected`, { method: "POST" });
    }
    expandedId = null;
    await refresh();
    await plan();
  } catch (e) {
    alert(e.message);
    btn.disabled = false;
  }
});

async function changeSector(id, sector) {
  await api(`/api/reports/${id}/sector`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ official: official(), sector }) });
  expandedId = Number(id);
  await refresh();
  await plan();
}

$("#tickets").addEventListener("change", async (ev) => {
  const secSel = ev.target.closest("select[data-act=sector]");
  if (secSel) {
    secSel.disabled = true;
    try { await changeSector(secSel.closest(".ticket").dataset.id, Number(secSel.value)); }
    catch (e) { alert(e.message); await refresh(); }
    return;
  }
  const sel = ev.target.closest("select[data-act=lang]");
  if (!sel || !sel.value) return;
  const card = sel.closest(".ticket");
  sel.disabled = true;
  try {
    const d = await api(`/api/reports/${card.dataset.id}/redraft`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang: sel.value }) });
    $(".t-title", card).value = d.title;
    $(".t-body", card).value = d.body;
  } catch (e) { alert(e.message); }
  sel.disabled = false;
});

document.querySelectorAll(".filters button").forEach((b) => b.addEventListener("click", () => setFilter(b.dataset.f)));

/* ---------- report dialog ---------- */
const dlg = $("#reportDialog");
$("#openReport").addEventListener("click", () => { dlg.showModal(); });
$("#closeReport").addEventListener("click", () => dlg.close());
dlg.addEventListener("click", (ev) => { if (ev.target === dlg) dlg.close(); });

$("#photo").addEventListener("change", () => {
  const f = $("#photo").files[0];
  if (!f) return;
  const img = $("#preview");
  img.src = URL.createObjectURL(f);
  img.hidden = false;
  $("#dropText").hidden = true;
});

function getGps() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition((p) => resolve(p.coords), () => resolve(null), { timeout: 8000 });
  });
}

function nearestSector(lat, lon) {
  const rad = Math.PI / 180;
  const dist = (s) => {
    const dp = (s.lat - lat) * rad, dl = (s.lon - lon) * rad;
    const a = Math.sin(dp / 2) ** 2 + Math.cos(lat * rad) * Math.cos(s.lat * rad) * Math.sin(dl / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.sqrt(a));
  };
  let best = null, bestD = Infinity;
  for (const s of sectorList) { const d = dist(s); if (d < bestD) { best = s; bestD = d; } }
  return best && bestD <= 3000 ? best : null; // farther than 3 km from every centre: not in a sector
}

$("#useGps").addEventListener("change", async () => {
  const note = $("#gpsMsg");
  gpsFix = null;
  note.textContent = "";
  if (!$("#useGps").checked) return;
  note.textContent = "Finding your location…";
  const c = await getGps();
  if (!$("#useGps").checked) return;
  if (!c) { note.textContent = "Could not get your location. Choose the sector yourself."; return; }
  gpsFix = c;
  const s = nearestSector(c.latitude, c.longitude);
  if (!s) { note.textContent = "Your location is outside Chandigarh's sectors. Choose the sector yourself."; return; }
  $("#sector").value = String(s.sector);
  note.textContent = `Sector set to ${s.sector} from your location (you can change it).`;
});
$("#sector").addEventListener("change", () => { $("#gpsMsg").textContent = ""; });

$("#reportForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = $("#formMsg"), btn = $("#submitBtn"), f = $("#photo").files[0];
  msg.className = "hint";
  if (!f) { msg.textContent = "Please choose a photo."; msg.className = "hint err"; return; }
  if (f.size > 8 * 1024 * 1024) { msg.textContent = "Photo is larger than 8 MB."; msg.className = "hint err"; return; }
  if (!$("#sector").value) { msg.textContent = "Please choose a sector."; msg.className = "hint err"; return; }
  try { localStorage.setItem("sector", $("#sector").value); } catch { /* storage blocked */ }
  const fd = new FormData();
  fd.append("photo", f);
  fd.append("sector", $("#sector").value);
  fd.append("lang", $("#lang").value);
  if ($("#useGps").checked) {
    const c = gpsFix || await getGps();
    if (c) { fd.append("lat", c.latitude); fd.append("lon", c.longitude); }
  }
  btn.disabled = true;
  const t0 = Date.now();
  const tick = setInterval(() => { msg.textContent = `Analysing photo with Gemma 4… ${Math.round((Date.now() - t0) / 1000)} s`; }, 500);
  try {
    const r = await api("/api/reports", { method: "POST", body: fd });
    clearInterval(tick);
    if (r.created === false) {
      msg.textContent = r.message;
      $("#result").hidden = false;
      $("#result").innerHTML = `<h3>No ticket created</h3><p class="hint">${esc(r.message)}</p><p class="hint">Why: ${esc(r.reason)}</p>`;
      return;
    }
    msg.textContent = `Done in ${Math.round((Date.now() - t0) / 1000)} s. Draft ticket #${r.id} is waiting for an official's approval.`;
    showResult(r);
    await refresh();
    setFilter("pending_review");
    if (markers.has(r.id)) map.flyTo(markers.get(r.id).getLatLng(), 15);
    focusTicket(r.id, false);
  } catch (e) {
    clearInterval(tick);
    msg.textContent = e.message;
    msg.className = "hint err";
  } finally {
    btn.disabled = false;
  }
});

function showResult(r) {
  const box = $("#result");
  box.hidden = false;
  const done = `<div class="actions"><button type="button" class="primary" id="viewTicket">View ticket</button></div>`;
  if (r.vision_status !== "ok") {
    box.innerHTML = `<h3>Sent for human review</h3><p class="hint">The model's answer could not be validated, so an official will classify this photo manually. Nothing is lost.</p>${done}`;
  } else {
    box.innerHTML = `<h3>What the model saw</h3>
      <dl class="kv">
        <dt>Type</dt><dd>${esc(KIND[r.kind])}</dd>
        <dt>Material</dt><dd>${esc(WASTE[r.waste_type])}</dd>
        <dt>Severity</dt><dd>${r.severity} of 5</dd>
        <dt>Confidence</dt><dd>${Math.round(r.confidence * 100)}%</dd>
        <dt>Why</dt><dd>${esc(r.reason)}</dd>
        ${r.score != null ? `<dt>Score</dt><dd>${r.score} of 100</dd>` : ""}
        ${r.image_blurred ? `<dt>Privacy</dt><dd>Face or number plate detected: the public city view shows a blurred copy.</dd>` : ""}
      </dl>${done}`;
  }
  $("#viewTicket").addEventListener("click", () => { dlg.close(); focusTicket(r.id); });

  // A similar open ticket nearby: ask, never merge on our own.
  const similar = Array.isArray(r.similar) ? r.similar : [];
  if (!similar.length) return;
  const ask = document.createElement("div");
  ask.className = "similar";
  ask.innerHTML = `<h3>Is this the same spot?</h3>
    ${similar.map((s) => `<div class="s-row"><span>A similar report is already open nearby: <b>#${s.id}</b> ${esc(KIND[s.kind] || "")}, Sector ${esc(s.sector)} (${s.report_count} report${s.report_count > 1 ? "s" : ""})${s.distance_m != null ? `, ${s.distance_m} m away` : ""}.</span>
      <button type="button" class="primary" data-merge="${s.id}">Yes, add to it</button></div>`).join("")}
    <button type="button" data-nomerge>No, new spot</button>`;
  box.appendChild(ask);
  ask.addEventListener("click", async (ev) => {
    if (ev.target.closest("[data-nomerge]")) { ask.remove(); return; }
    const b = ev.target.closest("[data-merge]");
    if (!b) return;
    const into = Number(b.dataset.merge);
    b.disabled = true;
    try {
      await api(`/api/reports/${r.id}/merge`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ into }) });
      box.innerHTML = `<h3>Thanks, your report was added to #${into}</h3><p class="hint">It raises that ticket's report count. The ticket's priority score is not changed.</p>
        <div class="actions"><button type="button" class="primary" id="viewTicket">View ticket</button></div>`;
      $("#viewTicket").addEventListener("click", () => { dlg.close(); focusTicket(into); });
      $("#formMsg").textContent = `Your report was added to ticket #${into}.`;
      await refresh();
      await plan();
    } catch (e) {
      b.disabled = false;
      alert(e.message);
    }
  });
}

/* ---------- routes ---------- */
function drawPlan(p) {
  routeLayer.clearLayers();
  baseLayer.clearLayers();
  p.routes.forEach((r, i) => {
    if (!r.path.length) return;
    const color = TRUCK_COLORS[i % TRUCK_COLORS.length];
    L.polyline(r.road_path || r.path, { color, weight: 5, opacity: .85 }).bindTooltip(`${r.truck}: ${r.stop_ids.length} stops, ${r.distance_km} km`).addTo(routeLayer);
    r.path.slice(1, -1).forEach((pt, k) => L.marker(pt, {
      icon: L.divIcon({ className: "", html: `<div style="background:${color};color:#fff;border-radius:50%;width:18px;height:18px;font-size:11px;font-weight:700;display:grid;place-items:center;border:2px solid #fff">${k + 1}</div>`, iconSize: [18, 18], iconAnchor: [9, -6] }),
      interactive: false,
    }).addTo(routeLayer));
  });
  p.baseline.forEach((r) => {
    if (r.path.length) L.polyline(r.road_path || r.path, { color: "#555", weight: 3, dashArray: "6 6", opacity: .8 }).addTo(baseLayer);
  });
  if ($("#showBase").checked) baseLayer.addTo(map);
}

async function plan() {
  const btn = $("#planBtn"), box = $("#savings");
  btn.disabled = true;
  try {
    const p = await api("/api/routes/plan", { method: "POST" });
    lastPlan = p;
    updateStats();
    if (!p.stops) { routeLayer?.clearLayers(); baseLayer?.clearLayers(); box.hidden = false; box.innerHTML = `<div class="empty">${esc(p.note)}</div>`; return; }
    drawPlan(p);
    box.hidden = false;
    box.innerHTML = `
      <div class="trucks">${p.routes.map((r, i) => `<div class="truck" style="border-left-color:${TRUCK_COLORS[i % TRUCK_COLORS.length]}"><b>Truck ${esc(r.truck)} · ${r.stop_ids.length} stops</b><span>Load ${r.load} of ${r.capacity} · ${r.distance_km} km</span></div>`).join("")}</div>
      ${p.dropped.length ? `<div class="simnote">Fleet is full: the lowest-score tickets ${p.dropped.map((d) => "#" + d).join(", ")} wait for the next run.</div>` : ""}
      <div class="simnote"><b>Simulation.</b> ${esc(p.note)} The fixed route visits the same stops in sector-number order.</div>`;
  } catch (e) {
    box.hidden = false;
    box.innerHTML = `<div class="empty">Route planning failed: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}
$("#planBtn").addEventListener("click", plan);
$("#showBase").addEventListener("change", (e) => { if (e.target.checked) baseLayer.addTo(map); else map.removeLayer(baseLayer); });

/* ---------- boot ---------- */
(async function boot() {
  try { $("#official").value = localStorage.getItem("official") || ""; } catch { /* storage blocked */ }
  loadStatus();
  try { await initMap(); } catch (e) { $("#map").innerHTML = `<div class="empty">Map unavailable: ${esc(e.message)}</div>`; }
  await refresh();
  if (location.hash.includes("base")) $("#showBase").checked = true;
  await plan(); // routes for already-approved tickets are drawn on load
})();

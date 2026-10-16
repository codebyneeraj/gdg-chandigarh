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
    if (r.status === "rejected" || r.status === "collected" || r.kind === "clean" || !r.kind) continue;
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
  return `<div class="breakdown">
    <div class="bar" aria-hidden="true">${parts.map(([k, v]) => `<i class="b-${k}" style="width:${v}%"></i>`).join("")}</div>
    Severity ${b.severity} + age ${b.age} + repeats ${b.repeats} (${b.repeat_count} nearby) + site ${b.site} (${esc(b.site_reason)}) = <b>${b.total}</b> of 100
  </div>`;
}

function statusPill(r) {
  return r.status === "pending_review" ? `<span class="pill review">Review</span>` : `<span class="pill">${esc(r.status.replace("_", " "))}</span>`;
}

function compactHtml(r) {
  const needs = r.vision_status !== "ok";
  const title = r.kind ? `${esc(KIND[r.kind])} · Sector ${r.sector}` : `Sector ${r.sector} · needs a human look`;
  const sub = r.kind
    ? `${esc(WASTE[r.waste_type] || r.waste_type)} · severity ${r.severity}${r.synthetic ? " · synthetic" : ""}`
    : "The model's answer could not be validated";
  const box = needs
    ? `<div class="score" style="background:#e4def9;color:#3a2fa3">?</div>`
    : `<div class="score" style="background:${scoreHex(r)}">${r.score != null ? Math.round(r.score) : "–"}</div>`;
  return `<button type="button" class="ticket compact${needs ? " review-needed" : ""}" id="t-${r.id}" data-id="${r.id}" data-expand aria-label="Open ticket ${r.id}">
    ${box}<div class="c-main"><div class="ttitle">${title}</div><div class="meta">${sub}</div></div>${statusPill(r)}
  </button>`;
}

function ticketHtml(r) {
  const pending = r.status === "pending_review";
  const review = r.vision_status !== "ok";
  const pills = [
    r.synthetic ? `<span class="pill synth">synthetic</span>` : "",
    review ? `<span class="pill review">needs human review</span>` : "",
    r.image_blurred ? `<span class="pill blur">photo blurred (privacy)</span>` : "",
    statusPill(r),
  ].join("");
  const scoreBox = `<div class="score" style="background:${scoreHex(r)}">${r.score != null ? Math.round(r.score) : "–"}<small>score</small></div>`;
  const thumb = r.has_image ? `<img class="thumb" loading="lazy" src="/api/reports/${r.id}/image" alt="Reported photo #${r.id}">` : "";
  const facts = r.kind
    ? `${esc(WASTE[r.waste_type] || r.waste_type)} · severity ${r.severity} of 5 · confidence ${Math.round((r.confidence || 0) * 100)}%`
    : `Model could not analyse this photo${r.vision_error ? `: ${esc(r.vision_error.slice(0, 120))}` : ""}`;
  const draft = pending && r.ticket_title
    ? `<div class="draft"><div class="draft-label">Draft ticket · edit before approving</div>
        <input class="t-title" value="${esc(r.ticket_title)}" maxlength="120" aria-label="Ticket title">
        <textarea class="t-body" maxlength="1200" aria-label="Ticket text">${esc(r.ticket_body)}</textarea></div>`
    : r.ticket_title ? `<div class="draft"><div class="ttitle">${esc(r.ticket_title)}</div><div class="body">${esc(r.ticket_body)}</div></div>` : "";
  const actions = pending ? `<div class="actions">
      ${r.ticket_title ? `<button type="button" class="primary" data-act="approve">Approve</button>` : ""}
      <button type="button" data-act="reject">Reject</button>
      ${r.kind && r.kind !== "clean" ? `<select data-act="lang" aria-label="Redraft in language">
        <option value="">Redraft in…</option><option value="en">English</option><option value="hi">हिन्दी</option><option value="pa">ਪੰਜਾਬੀ</option></select>` : ""}
    </div>` : r.status === "approved" ? `<div class="actions"><button type="button" data-act="collected">Mark collected</button></div>` : "";
  return `<article class="ticket open" id="t-${r.id}" data-id="${r.id}">
    <div class="thead">${scoreBox}<div class="c-main">
      <div class="ttitle">${r.kind ? esc(KIND[r.kind]) : "Needs a human look"} · Sector ${r.sector}</div>
      <div class="meta">${pills}</div>
      <div class="meta">${facts}</div>
      ${r.reason ? `<div class="meta reason">“${esc(r.reason)}”</div>` : ""}
      <div class="meta">#${r.id} · Vision: ${esc((r.vision_model || "–").replace("gemini-api:", ""))}${r.ticket_model ? ` · Draft: ${esc(r.ticket_model.replace("gemini-api:", ""))}` : ""}${r.reviewed_by ? ` · Reviewed by ${esc(r.reviewed_by)}` : ""}</div>
    </div>${thumb}</div>
    ${breakdownHtml(r.score_breakdown)}
    ${draft}
    ${actions}
  </article>`;
}

function visibleList() {
  return reports.filter((r) => filter === "all" || r.status === filter);
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

function focusTicket(id, scroll = true) {
  const r = reports.find((x) => x.id === id);
  if (!r) return;
  if (filter !== "all" && r.status !== filter) { filter = "all"; document.querySelectorAll(".filters button").forEach((b) => b.classList.toggle("on", b.dataset.f === "all")); }
  expandedId = id;
  drawTickets();
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

$("#tickets").addEventListener("click", async (ev) => {
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

$("#tickets").addEventListener("change", async (ev) => {
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
  sel.value = "";
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
    const c = await getGps();
    if (c) { fd.append("lat", c.latitude); fd.append("lon", c.longitude); }
  }
  btn.disabled = true;
  const t0 = Date.now();
  const tick = setInterval(() => { msg.textContent = `Analysing photo with Gemma 4… ${Math.round((Date.now() - t0) / 1000)} s`; }, 500);
  try {
    const r = await api("/api/reports", { method: "POST", body: fd });
    clearInterval(tick);
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
        ${r.image_blurred ? `<dt>Privacy</dt><dd>Face or number plate detected: only a blurred copy was kept.</dd>` : ""}
      </dl>${done}`;
  }
  $("#viewTicket").addEventListener("click", () => { dlg.close(); focusTicket(r.id); });
}

/* ---------- routes ---------- */
function drawPlan(p) {
  routeLayer.clearLayers();
  baseLayer.clearLayers();
  p.routes.forEach((r, i) => {
    if (!r.path.length) return;
    const color = TRUCK_COLORS[i % TRUCK_COLORS.length];
    L.polyline(r.path, { color, weight: 5, opacity: .85 }).bindTooltip(`${r.truck}: ${r.stop_ids.length} stops, ${r.distance_km} km`).addTo(routeLayer);
    r.path.slice(1, -1).forEach((pt, k) => L.marker(pt, {
      icon: L.divIcon({ className: "", html: `<div style="background:${color};color:#fff;border-radius:50%;width:18px;height:18px;font-size:11px;font-weight:700;display:grid;place-items:center;border:2px solid #fff">${k + 1}</div>`, iconSize: [18, 18], iconAnchor: [9, -6] }),
      interactive: false,
    }).addTo(routeLayer));
  });
  p.baseline.forEach((r) => {
    if (r.path.length) L.polyline(r.path, { color: "#555", weight: 3, dashArray: "6 6", opacity: .8 }).addTo(baseLayer);
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

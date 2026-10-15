"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KIND = { overflowing_bin: "Overflowing bin", illegal_dump: "Illegal dumping", litter: "Litter", clean: "No waste" };
const WASTE = { mixed: "Mixed", dry_recyclable: "Dry / recyclable", wet_organic: "Wet / organic", construction_debris: "Construction debris",
  bulky: "Bulky items", e_waste: "E-waste", hazardous: "Hazardous", none: "None" };
const TRUCK_COLORS = ["#1565c0", "#00897b", "#8e24aa", "#f4511e", "#3949ab"];

let reports = [];
let filter = "pending_review";
let map, reportLayer, routeLayer, baseLayer, lastPlan = null;
const markers = new Map();

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error */ }
  if (!res.ok) throw new Error((body && body.detail && (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail))) || `Request failed (${res.status})`);
  return body;
}

function scoreColor(r) {
  if (r.status === "pending_review") return "var(--pend)";
  if (r.score == null) return "#888";
  return r.score >= 70 ? "var(--high)" : r.score >= 40 ? "var(--mid)" : "var(--low)";
}
function scoreHex(r) {
  if (r.status === "pending_review") return "#6b5bd2";
  return r.score >= 70 ? "#c62828" : r.score >= 40 ? "#ef8a17" : "#2e7d32";
}

/* ---------- status ---------- */
async function loadStatus() {
  try {
    const h = await api("/api/health");
    const v = h.vision, d = h.drafting;
    $("#status").innerHTML =
      `<span class="chip ${v.available ? "ok" : "bad"}">Vision: ${esc(v.model)}</span>` +
      `<span class="chip ${d.model !== "template" ? "ok" : "bad"}">Drafting: ${esc(d.model)}</span>`;
  } catch {
    $("#status").innerHTML = `<span class="chip bad">Server unreachable</span>`;
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
    icon: L.divIcon({ className: "", html: `<div style="background:#17201b;color:#fff;border:2px solid #fff;border-radius:6px;padding:1px 6px;font-size:12px;font-weight:700;white-space:nowrap">DEPOT</div>`, iconSize: [56, 22], iconAnchor: [28, 11] }),
  }).bindPopup(esc(depot.name)).addTo(map);
  reportLayer = L.layerGroup().addTo(map);
  routeLayer = L.layerGroup().addTo(map);
  baseLayer = L.layerGroup();
}

function drawReports() {
  reportLayer.clearLayers();
  markers.clear();
  for (const r of reports) {
    if (r.status === "rejected" || r.status === "collected" || r.kind === "clean" || !r.kind) continue;
    const radius = 6 + (r.score || 20) / 12;
    const m = L.circleMarker([r.lat, r.lon], { radius, color: "#fff", weight: 2, fillColor: scoreHex(r), fillOpacity: .9 })
      .bindTooltip(`#${r.id} · ${KIND[r.kind] || "?"} · score ${r.score ?? "–"}${r.synthetic ? " · synthetic" : ""}`)
      .on("click", () => focusTicket(r.id));
    m.addTo(reportLayer);
    markers.set(r.id, m);
  }
}

/* ---------- tickets ---------- */
function breakdownHtml(b) {
  if (!b) return "";
  const parts = [["sev", b.severity, "severity"], ["age", b.age, "age"], ["rep", b.repeats, "repeats nearby"], ["site", b.site, "site"]];
  return `<div class="breakdown">
    <div class="bar" aria-hidden="true">${parts.map(([k, v]) => `<i class="b-${k}" style="width:${v}%"></i>`).join("")}</div>
    Severity ${b.severity} + age ${b.age} + repeats ${b.repeats} (${b.repeat_count} nearby) + site ${b.site} (${esc(b.site_reason)}) = <b>${b.total}</b>/100
  </div>`;
}

function ticketHtml(r) {
  const pending = r.status === "pending_review";
  const review = r.vision_status !== "ok";
  const pills = [
    r.synthetic ? `<span class="pill synth">synthetic</span>` : "",
    review ? `<span class="pill review">needs human review</span>` : "",
    r.image_blurred ? `<span class="pill blur">photo blurred (privacy)</span>` : "",
    `<span class="pill">${esc(r.status.replace("_", " "))}</span>`,
  ].join("");
  const scoreBox = `<div class="score" style="background:${scoreColor(r)}">${r.score ?? "–"}<small>score</small></div>`;
  const thumb = r.has_image ? `<img class="thumb" loading="lazy" src="/api/reports/${r.id}/image" alt="Reported photo #${r.id}">` : "";
  const facts = r.kind
    ? `${esc(KIND[r.kind])} · ${esc(WASTE[r.waste_type] || r.waste_type)} · severity ${r.severity}/5 · confidence ${Math.round((r.confidence || 0) * 100)}%`
    : `Model could not analyse this photo${r.vision_error ? `: ${esc(r.vision_error.slice(0, 120))}` : ""}`;
  const body = pending && r.ticket_title
    ? `<input class="t-title" value="${esc(r.ticket_title)}" maxlength="120" aria-label="Ticket title">
       <textarea class="t-body" maxlength="1200" aria-label="Ticket text">${esc(r.ticket_body)}</textarea>`
    : r.ticket_title ? `<div class="ttitle">${esc(r.ticket_title)}</div><div class="body">${esc(r.ticket_body)}</div>` : "";
  const actions = pending ? `<div class="actions">
      ${r.ticket_title ? `<button class="primary" data-act="approve">Approve</button>` : ""}
      <button data-act="reject">Reject</button>
      ${r.kind && r.kind !== "clean" ? `<select data-act="lang" aria-label="Redraft in language">
        <option value="">Redraft in…</option><option value="en">English</option><option value="hi">हिन्दी</option><option value="pa">ਪੰਜਾਬੀ</option></select>` : ""}
    </div>` : r.status === "approved" ? `<div class="actions"><button data-act="collected">Mark collected</button></div>` : "";
  return `<article class="ticket" id="t-${r.id}" data-id="${r.id}">
    <div class="thead">${scoreBox}<div style="flex:1;min-width:0">
      <div class="ttitle">#${r.id} · Sector ${r.sector}</div>
      <div class="meta">${pills}</div>
      <div class="meta">${facts}</div>
      ${r.reason ? `<div class="meta">“${esc(r.reason)}”</div>` : ""}
      <div class="meta">Vision: ${esc(r.vision_model || "–")}${r.ticket_model ? ` · Draft: ${esc(r.ticket_model)}` : ""}${r.reviewed_by ? ` · Reviewed by ${esc(r.reviewed_by)}` : ""}</div>
    </div>${thumb}</div>
    ${breakdownHtml(r.score_breakdown)}
    ${body}
    ${actions}
  </article>`;
}

function drawTickets() {
  const list = reports.filter((r) => filter === "all" || r.status === filter);
  $("#tickets").innerHTML = list.length ? list.map(ticketHtml).join("") :
    `<div class="empty">${filter === "pending_review" ? "No tickets waiting for review. New citizen reports will appear here." :
      filter === "approved" ? "No approved tickets. Approve tickets to plan routes." : "No reports yet."}</div>`;
}

async function refresh() {
  try {
    reports = await api("/api/reports");
    drawReports();
    drawTickets();
  } catch (e) {
    $("#tickets").innerHTML = `<div class="empty">Could not load tickets: ${esc(e.message)}</div>`;
  }
}

function focusTicket(id) {
  const r = reports.find((x) => x.id === id);
  if (r && filter !== "all" && r.status !== filter) { setFilter("all"); }
  const el = $(`#t-${id}`);
  if (!el) return;
  document.querySelectorAll(".ticket.focus").forEach((t) => t.classList.remove("focus"));
  el.classList.add("focus");
  el.scrollIntoView({ behavior: "smooth", block: "center" });
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
    await refresh();
    if (lastPlan) await plan();
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

/* ---------- report form ---------- */
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
    focusTicket(r.id);
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
  if (r.vision_status !== "ok") {
    box.innerHTML = `<h3>Sent for human review</h3><p class="hint">The model's answer could not be validated, so an official will classify this photo manually. Nothing is lost.</p>`;
    return;
  }
  box.innerHTML = `<h3>What the model saw</h3>
    <dl class="kv">
      <dt>Type</dt><dd>${esc(KIND[r.kind])}</dd>
      <dt>Material</dt><dd>${esc(WASTE[r.waste_type])}</dd>
      <dt>Severity</dt><dd>${r.severity}/5</dd>
      <dt>Confidence</dt><dd>${Math.round(r.confidence * 100)}%</dd>
      <dt>Why</dt><dd>${esc(r.reason)}</dd>
      ${r.score != null ? `<dt>Score</dt><dd>${r.score}/100</dd>` : ""}
      ${r.image_blurred ? `<dt>Privacy</dt><dd>Face or number plate detected: only a blurred copy was kept.</dd>` : ""}
    </dl>`;
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
    box.hidden = false;
    if (!p.stops) { routeLayer.clearLayers(); baseLayer.clearLayers(); box.innerHTML = `<div class="empty" style="grid-column:1/-1">${esc(p.note)}</div>`; return; }
    drawPlan(p);
    box.innerHTML = `
      <div class="stat"><b>${p.stops}</b><span>approved tickets routed</span></div>
      <div class="stat"><b>${p.optimised_km} km</b><span>optimised (OR-Tools)</span></div>
      <div class="stat"><b>${p.baseline_km} km</b><span>naive fixed order</span></div>
      <div class="stat sim"><b>${p.saved_pct}%</b><span>shorter · ${p.saved_km} km saved</span></div>
      <div class="trucks">${p.routes.map((r, i) => `<span style="background:${TRUCK_COLORS[i % TRUCK_COLORS.length]}">${esc(r.truck)}: ${r.stop_ids.length} stops · load ${r.load}/${r.capacity} · ${r.distance_km} km</span>`).join("")}</div>
      ${p.dropped.length ? `<div class="simnote">Fleet capacity exceeded: lowest-score tickets ${p.dropped.map((d) => "#" + d).join(", ")} wait for the next run.</div>` : ""}
      <div class="simnote"><b>SIMULATION.</b> ${esc(p.note)} Baseline visits the same stops in sector-number order.</div>`;
  } catch (e) {
    box.hidden = false;
    box.innerHTML = `<div class="empty" style="grid-column:1/-1">Route planning failed: ${esc(e.message)}</div>`;
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
  // Deep links for demos/screenshots: #plan plans routes on load, #base also shows the naive route.
  if (location.hash.includes("base")) $("#showBase").checked = true;
  if (location.hash.includes("plan")) await plan();
})();

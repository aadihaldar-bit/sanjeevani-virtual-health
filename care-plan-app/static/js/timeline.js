// Patient timeline: one chronological feed of everything the voice assistant and the pharmacy have done for a patient.
// Reads the same record as patient.js (calls, visits, follow-up calls, prescriptions, orders, feedback) and merges it by time.
"use strict";

const TL_KINDS = {
  call: { label: "Calls", icon: "phone" },
  visit: { label: "Visits", icon: "calendar" },
  medicine: { label: "Medicines", icon: "pill" },
  order: { label: "Orders", icon: "truck" },
};
let tlEvents = [];
let tlFilter = "all";

// The voicebot sends UTC times without a zone.
const tlTime = (iso) => (iso ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z").getTime() : NaN);
const tlDay = (ms) => new Date(ms).toLocaleDateString("en-GB", { timeZone: "Asia/Dubai", weekday: "long", day: "numeric", month: "long", year: "numeric" });
const tlClock = (ms) => new Date(ms).toLocaleTimeString("en-GB", { timeZone: "Asia/Dubai", hour: "2-digit", minute: "2-digit" });
const tlLen = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "");

function buildTimeline(d) {
  const ev = [];
  (d.calls || []).forEach((c) => {
    const at = tlTime(c.started_at);
    const missed = !c.duration_seconds && c.status !== "in_progress";
    ev.push({
      at, kind: "call", title: c.direction === "inbound" ? "Patient called in" : "Care assistant called",
      body: missed ? "" : c.summary || "No summary for this call.",
      meta: [tlLen(c.duration_seconds), c.sentiment && humanise(c.sentiment)].filter(Boolean),
      status: missed ? "no_answer" : "", quiet: missed, callId: missed ? null : c.id,
    });
  });
  (d.follow_up_calls || []).forEach((t) => {
    if (t.status !== "pending") return; // finished attempts already show up as calls
    ev.push({
      at: tlTime(t.due_at), kind: "call",
      title: t.kind === "explain" ? "Call planned: explaining the medicines" : t.meeting_id ? "Call planned: feedback after the visit" : "Call planned: how are you doing?",
      body: "", meta: [], status: "scheduled",
    });
  });
  (d.appointments || []).forEach((a) => {
    ev.push({
      at: tlTime(a.slot_start || a.created_at), kind: "visit", title: a.doctor_name || "Doctor to be confirmed",
      body: [a.specialty, a.reason].filter(Boolean).join(" · "),
      meta: [a.fee_amount != null && `AED ${a.fee_amount}`, a.source === "inbound" ? "Booked on a call" : "From a follow-up call"].filter(Boolean),
      status: a.status,
    });
  });
  (d.prescriptions || []).forEach((rx) => {
    const names = (rx.items || []).map((i) => [i.drug_name, i.strength && i.strength !== "Standard" ? i.strength : ""].filter(Boolean).join(" ")).filter(Boolean);
    ev.push({
      at: tlTime(rx.issued_at), kind: "medicine", title: rx.doctor_name ? `Prescription from ${rx.doctor_name}` : "Prescription received",
      body: names.join(", "), meta: [rx.facility].filter(Boolean), status: rx.status,
    });
  });
  (d.orders || []).forEach((o) => {
    const items = Array.isArray(o.items) ? o.items.map((i) => i.drug_name || i.name).filter(Boolean).join(", ") : "";
    ev.push({
      at: tlTime(o.delivery_start || o.created_at), kind: "order", title: o.reference || "Pharmacy order",
      body: items || o.requested || "", meta: [o.amount != null && `AED ${o.amount}`, o.delivery_slot].filter(Boolean), status: o.status,
    });
  });
  (d.feedback || []).forEach((f) => {
    ev.push({
      at: tlTime(f.created_at), kind: "medicine", title: `Feedback on the medicines${f.satisfaction ? ` · ${f.satisfaction}/5` : ""}`,
      body: [f.taken && `Taken: ${humanise(f.taken)}`, f.helped && `Helped: ${humanise(f.helped)}`, f.side_effects && `Side effects: ${f.side_effects}`].filter(Boolean).join(" · "),
      meta: [], status: f.wants_doctor ? "needs_human" : "",
    });
  });
  return ev.filter((e) => !Number.isNaN(e.at));
}

function tlRow(e) {
  const k = TL_KINDS[e.kind];
  return `<li class="tl-item${e.quiet ? " is-quiet" : ""}">
    <span class="tl-dot">${icon(k.icon)}</span>
    <div class="tl-body">
      <div class="tl-head"><b>${esc(e.title)}</b>${chipFor(e.status)}<time>${esc(tlClock(e.at))}</time></div>
      ${e.body ? `<p>${esc(e.body)}</p>` : ""}
      ${e.meta.length ? `<small>${e.meta.map(esc).join(" · ")}</small>` : ""}
      ${e.callId ? `<button type="button" class="link plain tl-tr-btn" onclick="toggleTranscript(${e.callId}, this)">Show transcript</button><div class="tl-tr" hidden></div>` : ""}
    </div>
  </li>`;
}

function renderTimelineList() {
  const now = Date.now();
  const shown = tlEvents.filter((e) => tlFilter === "all" || e.kind === tlFilter);
  // The same planned call can be booked more than once; show it once. Runs of missed calls become one line.
  const seen = new Set();
  const upcoming = shown.filter((e) => e.at > now).sort((a, b) => a.at - b.at)
    .filter((e) => { const k = `${e.title}|${Math.floor(e.at / 60000)}`; if (seen.has(k)) return false; seen.add(k); return true; });
  const past = [];
  shown.filter((e) => e.at <= now).sort((a, b) => b.at - a.at).forEach((e) => {
    const prev = past[past.length - 1];
    if (e.quiet && prev && prev.quiet && tlDay(prev.at) === tlDay(e.at)) {
      prev.missed = (prev.missed || 1) + 1;
      prev.title = `Care assistant tried ${prev.missed} times, no answer`;
      prev.status = "";
    } else past.push({ ...e });
  });
  if (!shown.length) {
    $("tlBody").innerHTML = `<div class="empty"><b>Nothing here yet</b>Calls, visits, medicines and orders will appear as they happen.</div>`;
    return;
  }
  let html = "";
  if (upcoming.length) html += `<h3 class="tl-day">Coming up</h3><ol class="tl">${upcoming.map(tlRow).join("")}</ol>`;
  const byDay = new Map();
  past.forEach((e) => { const k = tlDay(e.at); if (!byDay.has(k)) byDay.set(k, []); byDay.get(k).push(e); });
  byDay.forEach((list, day) => { html += `<h3 class="tl-day">${esc(day)}</h3><ol class="tl">${list.map(tlRow).join("")}</ol>`; });
  $("tlBody").innerHTML = html;
}

const tlTranscripts = new Map();
async function toggleTranscript(id, btn) {
  const box = btn.nextElementSibling;
  if (!box.hidden) { box.hidden = true; btn.textContent = "Show transcript"; return; }
  box.hidden = false; btn.textContent = "Hide transcript";
  if (!tlTranscripts.has(id)) {
    box.innerHTML = `<div class="sk" style="height:46px"></div>`;
    try {
      const r = await fetch(`/api/v1/voice/call-transcript/${id}`);
      const d = await r.json();
      if (!r.ok) throw new Error(r.status === 501 ? "Transcripts are not available from the voice assistant yet." : d.detail || "Could not load the transcript.");
      tlTranscripts.set(id, d.transcript || []);
    } catch (err) {
      box.innerHTML = `<p class="small muted">${esc(err.message)}</p>`;
      return;
    }
  }
  const lines = tlTranscripts.get(id);
  box.innerHTML = lines.length ? lines.map((m) => `<div class="tl-line ${m.from === "agent" ? "is-agent" : ""}"><span>${m.from === "agent" ? "Care assistant" : "Patient"}</span>${esc(m.text)}</div>`).join("")
    : `<p class="small muted">No transcript was kept for this call.</p>`;
}
window.toggleTranscript = toggleTranscript;

function setTimelineFilter(f) {
  tlFilter = f;
  document.querySelectorAll("#tlFilter button").forEach((b) => b.classList.toggle("is-active", b.dataset.f === f));
  renderTimelineList();
}
window.setTimelineFilter = setTimelineFilter;

function renderTimeline(d) {
  tlEvents = buildTimeline(d);
  tlFilter = "all";
  const count = (k) => tlEvents.filter((e) => k === "all" || e.kind === k).length;
  $("tlFilter").innerHTML = [["all", "All"], ...Object.entries(TL_KINDS).map(([k, v]) => [k, v.label])]
    .map(([k, label]) => `<button type="button" data-f="${k}" class="${k === "all" ? "is-active" : ""}" onclick="setTimelineFilter('${k}')">${label}<span class="num">${count(k)}</span></button>`).join("");
  renderTimelineList();
}
window.renderTimeline = renderTimeline;

// Patient view: the record the Sanjeevani voice assistant holds for one patient (via /api/v1/voice/patient-record).
// The voicebot stores times in UTC without a zone; they are shown in Dubai time.
"use strict";

// $, esc and initials come from app.js, which loads first and declares them at top level.

let recordLoadedFor = null;

function dubai(iso, withTime = true) {
  if (!iso) return "";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
  return d.toLocaleString("en-GB", { timeZone: "Asia/Dubai", day: "numeric", month: "short", year: "numeric",
                                     ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}) });
}

const humanise = (s) => (s || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const STATUS_CHIP = {
  booked: "chip-good", done: "chip-good", completed: "chip-good", paid: "chip-good", delivered: "chip-good", confirmed: "chip-good",
  requested: "chip-accent", pending: "chip-accent", calling: "chip-accent", scheduled: "chip-accent", issued: "chip-accent",
  no_answer: "chip-warn", needs_human: "chip-warn", unpaid: "chip-warn", awaiting_prescription: "chip-warn", needs_clarification: "chip-warn",
  cancelled: "chip-bad", patient_declined: "chip-bad", failed: "chip-bad",
};
const chipFor = (s) => s ? `<span class="chip ${STATUS_CHIP[s] || ""}">${esc(humanise(s))}</span>` : "";
const emptyLine = (text) => `<p class="small muted" style="padding:6px 0">${esc(text)}</p>`;

function updateRosterHighlight(queryKey) {
  if (!queryKey) return;
  const k = queryKey.toLowerCase().replace(/[\s-]/g, "");
  document.querySelectorAll(".patient-roster-card").forEach((card) => {
    const q = (card.dataset.query || "").toLowerCase().replace(/[\s-]/g, "");
    const ext = (card.dataset.ext || "").toLowerCase();
    const phone = (card.dataset.phone || "").replace(/[\s-]/g, "");
    const matches = q === k || ext === k || (phone && phone === k);
    card.classList.toggle("is-selected", !!matches);
  });
}
window.updateRosterHighlight = updateRosterHighlight;

function renderPatientRosterCards(patients) {
  const container = $("patientRosterCards");
  if (!container) return;
  const countEl = $("patientRosterCount");
  if (countEl) countEl.textContent = patients && patients.length ? `${patients.length} registered` : "";

  if (!patients || !patients.length) {
    container.innerHTML = `<p class="tiny muted" style="padding:8px 0">No registered patients found.</p>`;
    return;
  }

  const currentQ = ($("recordQuery")?.value || "").trim().toLowerCase().replace(/[\s-]/g, "");

  container.innerHTML = patients.map((p) => {
    const name = p.name || `${p.first_name || ""} ${p.last_name || ""}`.trim() || "Unnamed Patient";
    const extId = p.external_id || "";
    const phone = p.phone_e164 || "";
    const queryKey = extId || phone;
    const cleanExt = extId.toLowerCase();
    const cleanPhone = phone.replace(/[\s-]/g, "");
    const isSelected = currentQ && (currentQ === cleanExt || currentQ === cleanPhone);

    return `
      <div class="patient-roster-card ${isSelected ? 'is-selected' : ''}" 
           data-query="${esc(queryKey)}"
           data-ext="${esc(extId)}"
           data-phone="${esc(phone)}"
           onclick="selectRosterPatient('${esc(queryKey)}')" 
           role="button" 
           tabindex="0"
           title="Click to view full record for ${esc(name)}">
        <div style="display:flex;align-items:center;gap:10px">
          <span class="avatar avatar-sm">${esc(initials(name) || "?")}</span>
          <div style="min-width:0;flex:1">
            <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(name)}</div>
            <div class="tiny muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
              ${esc([extId, phone].filter(Boolean).join(" · "))}
            </div>
          </div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;font-size:11px">
          ${p.consent_calls ? '<span class="chip chip-good" style="padding:1px 6px;font-size:10px">Calls OK</span>' : '<span class="chip chip-warn" style="padding:1px 6px;font-size:10px">No call</span>'}
          <span style="color:var(--accent);font-weight:600;font-size:11px">View record &rarr;</span>
        </div>
      </div>
    `;
  }).join("");
}
window.renderPatientRosterCards = renderPatientRosterCards;

function selectRosterPatient(queryKey) {
  const q = $("recordQuery");
  if (q) q.value = queryKey;
  updateRosterHighlight(queryKey);
  recordLoadedFor = null;
  loadPatientRecord();
}
window.selectRosterPatient = selectRosterPatient;

async function loadPatientRoster() {
  const container = $("patientRosterCards");
  if (!container) return;

  try {
    const r = await fetch("/api/v1/patients");
    if (!r.ok) return;
    const d = await r.json();
    const list = d.patients || [];
    window.voicebotPatients = list;
    if (typeof VOICE_CONNECTED !== "undefined") VOICE_CONNECTED = true;
    const warn = $("recordNotConnected");
    if (warn) warn.hidden = true;
    if (typeof populatePatientPresets === "function") {
      populatePatientPresets(list);
    }
    renderPatientRosterCards(list);
  } catch (err) {
    console.warn("Could not load patient roster:", err);
  }
}
window.loadPatientRoster = loadPatientRoster;

async function openPatientView() {
  await loadPatientRoster();
  const q = $("recordQuery");
  if (q && (!q.value || q.value === "SNJ-98214")) {
    const curVal = ($("patient_id")?.value || "").trim() || ($("mobile_number")?.value || "").trim();
    if (curVal) {
      q.value = curVal;
    } else if (window.voicebotPatients && window.voicebotPatients.length > 0) {
      q.value = window.voicebotPatients[0].external_id || window.voicebotPatients[0].phone_e164 || "";
    }
  }
  const warn = $("recordNotConnected");
  if (warn && typeof VOICE_CONNECTED !== "undefined") warn.hidden = VOICE_CONNECTED;
  if (q && q.value && recordLoadedFor !== q.value) {
    await loadPatientRecord();
  }
}
window.openPatientView = openPatientView;

async function loadPatientRecord(e) {
  if (e) e.preventDefault();
  const q = $("recordQuery").value.trim();
  if (!q) { $("recordQuery").focus(); return; }
  updateRosterHighlight(q);
  const isPhone = /^\+?\d[\d\s-]{6,}$/.test(q);
  const params = new URLSearchParams(isPhone ? { phone: q.replace(/[\s-]/g, "") } : { patient_id: q });
  ["recordBody", "recordEmpty"].forEach((id) => { $(id).hidden = true; });
  $("recordLoading").hidden = false;
  try {
    const r = await fetch(`/api/v1/voice/patient-record?${params}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "the record could not be loaded");
    recordLoadedFor = q;
    if (typeof VOICE_CONNECTED !== "undefined") VOICE_CONNECTED = true;
    const warn = $("recordNotConnected");
    if (warn) warn.hidden = true;
    renderPatientRecord(d);
    if (d.patient) {
      updateRosterHighlight(d.patient.external_id || d.patient.phone || q);
    }
  } catch (err) {
    $("recordEmptyText").textContent = err.message.charAt(0).toUpperCase() + err.message.slice(1) + ".";
    $("recordEmpty").hidden = false;
  } finally {
    $("recordLoading").hidden = true;
  }
}
window.loadPatientRecord = loadPatientRecord;

function renderPatientRecord(d) {
  const p = d.patient || {};
  const name = p.name || "Unregistered caller";
  const upcoming = (d.appointments || []).filter((a) => {
    if (a.status === "done" || a.status === "cancelled") return false;
    const t = typeof slotMs === "function" ? slotMs(a) : (a.slot_start ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(a.slot_start) ? a.slot_start : a.slot_start + "Z").getTime() : NaN);
    return !Number.isNaN(t) && t >= Date.now();
  });
  const nextCall = (d.follow_up_calls || []).filter((t) => t.status === "pending").sort((a, b) => (a.due_at > b.due_at ? 1 : -1))[0];
  $("recordHead").innerHTML = `
    <span class="avatar avatar-lg">${esc(initials(name) || "?")}</span>
    <div class="rh-main">
      <h2>${esc(name)}</h2>
      <p class="muted small">${[p.external_id && `ID ${esc(p.external_id)}`, p.phone && esc(p.phone),
        p.guardian_name && `Guardian ${esc(p.guardian_name)}`, p.preferred_language && `Language ${esc(p.preferred_language.toUpperCase())}`]
        .filter(Boolean).join(" · ")}</p>
      <div class="chips" style="margin-top:8px">
        ${p.do_not_call ? `<span class="chip chip-bad">Do not call</span>` : p.consent_calls === false ? `<span class="chip chip-warn">No call consent</span>`
          : p.found ? `<span class="chip chip-good">${icon("check")}Agreed to calls</span>` : ""}
        ${!p.found ? `<span class="chip">Known from calls only</span>` : ""}
      </div>
    </div>
    <div class="rh-stats">
      <div><b class="num">${(d.calls || []).length}</b><span>calls</span></div>
      <div><b class="num">${upcoming.length}</b><span>upcoming visits</span></div>
      <div><b class="num">${(d.orders || []).length}</b><span>orders</span></div>
    </div>
    ${nextCall ? `<p class="rh-next">${icon("clock")}Next call from the care assistant: <b>${esc(dubai(nextCall.due_at))}</b> (${esc(nextCall.kind === "explain" ? "explaining the medicines" : "how you are doing")})</p>` : ""}`;

  $("recAppointments").innerHTML = (d.appointments || []).length ? d.appointments.map((a) => `
    <div class="rec-row">
      <div class="t"><b>${esc(a.doctor_name || "Doctor to be confirmed")}</b>
        <span>${esc([a.specialty, a.slot_start ? dubai(a.slot_start) : a.booked_slot].filter(Boolean).join(" · ") || "Time to be confirmed")}</span>
        ${a.reason ? `<span>${esc(a.reason)}</span>` : ""}
        ${a.fee_amount != null ? `<span>Fee AED ${esc(a.fee_amount)}${a.copay_amount != null ? `, you pay AED ${esc(a.copay_amount)}` : ""}${a.payment_status ? ` · ${esc(humanise(a.payment_status))}` : ""}</span>` : ""}
      </div>
      <div class="r">${chipFor(a.status)}<small>${a.completed_at ? `Done ${esc(dubai(a.completed_at, false))}` : a.source === "inbound" ? "Booked on a call" : "From a follow-up call"}</small></div>
    </div>`).join("") : emptyLine("No appointments booked through the voice assistant.");

  $("recFollowUps").innerHTML = (d.follow_up_calls || []).length ? d.follow_up_calls.map((t) => `
    <div class="rec-row">
      <div class="t"><b>${t.kind === "explain" ? "Explaining the medicines" : t.kind === "feedback" ? (t.meeting_id ? "Feedback call after the visit" : "How are you doing?") : esc(humanise(t.kind))}</b>
        <span>${esc(dubai(t.due_at))}${t.attempts ? ` · ${t.attempts} ${t.attempts === 1 ? "attempt" : "attempts"}` : ""}</span></div>
      <div class="r">${chipFor(t.status)}</div>
    </div>`).join("") : emptyLine("No follow-up calls booked.");

  $("recOrders").innerHTML = (d.orders || []).length ? d.orders.map((o) => {
    const items = Array.isArray(o.items) ? o.items.map((i) => i.drug_name || i.name).filter(Boolean).join(", ") : "";
    return `<div class="rec-row">
      <div class="t"><b>${esc(o.reference || "Order")}</b>
        <span>${esc(items || o.requested || "Items to be confirmed")}</span>
        <span>${esc([o.amount != null && `AED ${o.amount}`, o.delivery_start ? `Delivery ${dubai(o.delivery_start)}` : o.delivery_slot].filter(Boolean).join(" · "))}</span></div>
      <div class="r">${chipFor(o.status)}${o.payment_status && o.payment_status !== o.status ? chipFor(o.payment_status) : ""}</div>
    </div>`;
  }).join("") : emptyLine("No pharmacy orders placed by phone.");

  $("recPrescriptions").innerHTML = (d.prescriptions || []).length ? d.prescriptions.map((rx) => `
    <div class="rec-row">
      <div class="t"><b>${esc(rx.doctor_name || "Prescription")}</b>
        <span>${esc([dubai(rx.issued_at, false), rx.facility, rx.external_id].filter(Boolean).join(" · "))}</span>
        ${(rx.items || []).map((i) => `<span><b>${esc([i.drug_name, i.strength && i.strength !== "Standard" ? i.strength : ""].filter(Boolean).join(" "))}</b>${i.dose_text || i.timing || i.duration_days ? ` · ${esc([i.dose_text, i.timing, i.duration_days ? `${i.duration_days} days` : ""].filter(Boolean).join(", "))}` : ""}</span>`).join("")}</div>
      <div class="r">${chipFor(rx.status)}</div>
    </div>`).join("") : emptyLine("No prescriptions sent to the voice assistant.");

  $("recCalls").innerHTML = (d.calls || []).length ? d.calls.map((c) => `
    <div class="rec-row">
      <div class="t"><b>${c.direction === "inbound" ? "Patient called in" : "Care assistant called"} · ${esc(dubai(c.started_at))}</b>
        <span>${esc(c.summary || "No summary for this call.")}</span></div>
      <div class="r">${c.duration_seconds ? `<span class="small muted num">${Math.floor(c.duration_seconds / 60)}:${String(Math.round(c.duration_seconds % 60)).padStart(2, "0")}</span>` : ""}
        ${c.sentiment ? `<span class="chip">${esc(humanise(c.sentiment))}</span>` : ""}</div>
    </div>`).join("") : emptyLine("No calls with this number yet.");

  const fb = d.feedback || [];
  $("recFeedbackWrap").hidden = !fb.length;
  $("recFeedback").innerHTML = fb.map((f) => `
    <div class="rec-row">
      <div class="t"><b>${f.satisfaction ? "★".repeat(f.satisfaction) + "☆".repeat(5 - f.satisfaction) : "Feedback"}</b>
        <span>${esc([f.taken && `Taken: ${humanise(f.taken)}`, f.helped && `Helped: ${humanise(f.helped)}`].filter(Boolean).join(" · "))}</span>
        ${f.side_effects ? `<span>Side effects: ${esc(f.side_effects)}</span>` : ""}</div>
      <div class="r">${f.wants_doctor ? `<span class="chip chip-warn">Wants a doctor</span>` : ""}${f.refill_requested ? `<span class="chip chip-accent">Refill asked</span>` : ""}
        <small>${esc(dubai(f.created_at, false))}</small></div>
    </div>`).join("");
  if (typeof renderTimeline === "function") renderTimeline(d);
  if (typeof loadPatientVault === "function") loadPatientVault(p.external_id);
  $("recordBody").hidden = false;
}
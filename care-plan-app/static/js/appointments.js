// Appointments view: the doctor visits the voice assistant booked on incoming calls or called patients about, as a day-by-day list
// with a button to mark a visit done. Marking it done makes the voicebot schedule the patient's feedback call.
// Booking happens in a sheet: pick a patient and a doctor, a date, and a free time slot.
"use strict";

let apptDirection = "";
let apptFilter = "upcoming";
let apptQuery = "";
let apptTimer = null;
let apptDoctorIdx = 0;
window.apptList = [];

const FEEDBACK_LABEL = {
  pending: "Feedback call scheduled", calling: "Feedback call in progress", done: "Feedback call made",
  no_answer: "Feedback call: no answer", patient_declined: "Feedback call declined", needs_human: "Feedback call needs a person",
};
const SLOT_TIMES = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00", "17:30", "18:00"];

function apptMessage(text, bad) {
  const el = $("apptFormMsg");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = bad ? "var(--bad, #c0392b)" : "";
}

function apptNotice(title, text) {
  $("apptNoticeTitle").textContent = title || "";
  $("apptNoticeText").textContent = text || "";
  $("apptNotConnected").hidden = !title;
}

// ---------------------------------------------------------------- the list
const isOpen = (a) => a.status !== "done" && a.status !== "cancelled";
const timeParts = (a) => {
  const t = slotMs(a);
  if (Number.isNaN(t)) return { big: "Time to confirm", small: a.booked_slot || "" };
  return {
    big: new Date(t).toLocaleTimeString("en-US", { timeZone: "Asia/Dubai", hour: "numeric", minute: "2-digit" }),
    small: new Date(t).toLocaleDateString("en-GB", { timeZone: "Asia/Dubai", weekday: "short", day: "numeric", month: "short" }),
  };
};

function apptRow(a) {
  const { big, small } = timeParts(a);
  const inbound = a.source === "inbound";
  const fb = a.feedback_call;
  const query = a.patient_external_id || a.phone || "";
  const who = esc(a.patient_name || "Unknown caller");
  const sub = [a.doctor_name || "Doctor to be confirmed", a.specialty].filter(Boolean).join(" · ");
  return `
    <li class="appt ${isOpen(a) ? "" : "is-done"}" data-appt="${a.id}">
      <div class="appt-time"><b>${esc(big)}</b><span>${esc(small)}</span></div>
      <div class="appt-main">
        <b>${query ? `<a href="#patient" onclick="openPatientRecord('${esc(query)}');return false">${who}</a>` : who}</b>
        <span>${esc(sub)}</span>
        ${a.reason ? `<span class="appt-reason">${esc(a.reason)}</span>` : ""}
        <span class="appt-meta">${icon(inbound ? "phone" : "mic")}${inbound ? "Booked on a call" : "Assistant called"}${a.fee_amount != null ? ` · AED ${esc(a.fee_amount)}` : ""}${a.status !== "booked" ? "" : ""}
          ${a.status !== "booked" ? chipFor(a.status) : ""}${fb ? `<span class="chip ${STATUS_CHIP[fb.status] || ""}" title="${esc(dubai(fb.due_at))}">${esc(FEEDBACK_LABEL[fb.status] || humanise(fb.status))}</span>` : ""}</span>
      </div>
      <div class="appt-act">
        ${a.status === "done" ? `<button class="btn btn-secondary btn-sm" onclick="setAppointmentStatus(${a.id}, 'booked')">Reopen</button>`
          : a.status === "cancelled" ? ""
          : `<button class="btn btn-dark btn-sm" onclick="setAppointmentStatus(${a.id}, 'done')">${icon("check")}Mark done</button>
             <button class="link plain appt-cancel" onclick="setAppointmentStatus(${a.id}, 'cancelled')">Cancel</button>`}
      </div>
    </li>`;
}

function visibleAppointments() {
  const today = dubaiDay(Date.now());
  const q = apptQuery.trim().toLowerCase();
  return window.apptList.filter((a) => {
    const t = slotMs(a);
    const ok = apptFilter === "all" ? true
      : apptFilter === "done" ? !isOpen(a)
      : apptFilter === "today" ? isOpen(a) && !Number.isNaN(t) && dubaiDay(t) === today
      : isOpen(a) && (Number.isNaN(t) || t >= Date.now() - 36e5 || dubaiDay(t) === today);
    if (!ok) return false;
    return !q || [a.patient_name, a.doctor_name, a.reason, a.specialty, a.phone].some((v) => String(v || "").toLowerCase().includes(q));
  });
}

function renderApptStats() {
  const today = dubaiDay(Date.now());
  const open = window.apptList.filter(isOpen);
  const nToday = open.filter((a) => { const t = slotMs(a); return !Number.isNaN(t) && dubaiDay(t) === today; }).length;
  const nUp = open.filter((a) => { const t = slotMs(a); return Number.isNaN(t) || t >= Date.now() || dubaiDay(t) === today; }).length;
  const nDone = window.apptList.filter((a) => a.status === "done").length;
  $("apptStats").innerHTML = [["Today", nToday], ["Upcoming", nUp], ["Done", nDone]]
    .map(([l, n]) => `<div><b class="num">${n}</b><span>${l}</span></div>`).join("");
  document.querySelectorAll("#apptFilter button").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.f === apptFilter);
  });
}

function renderApptList() {
  const list = visibleAppointments();
  renderApptStats();
  if (!list.length) {
    $("apptList").innerHTML = `<div class="card empty"><b>${window.apptList.length ? "Nothing matches" : "No appointments yet"}</b>${
      window.apptList.length ? "Try another filter or search." : "Book one with the button above, or have a patient phone in."}</div>`;
    return;
  }
  $("apptList").innerHTML = groupAppointments(list).map((g) => `
    <section class="appt-group"><h3 class="tl-day">${esc(g.title)}</h3><ul class="card appt-list">${g.items.map(apptRow).join("")}</ul></section>`).join("");
  hydrateIcons($("apptList"));
}

async function loadAppointments() {
  $("apptLoading").hidden = false;
  try {
    const params = new URLSearchParams(apptDirection ? { source: apptDirection } : {});
    const r = await fetch(`/api/v1/voice/appointments?${params}`);
    const d = await r.json().catch(() => ({}));
    if (r.status === 503) { apptNotice(); $("apptNotConnected").hidden = false; $("apptList").innerHTML = ""; return; }
    if (!r.ok) throw new Error(d.detail || "the appointments could not be loaded");
    apptNotice();
    window.apptList = d.appointments || [];
    if (typeof bookKnown !== "undefined" && bookKnown) window.apptList.forEach((a) => bookKnown.add(a.id));   // listed means seen
    renderApptList();
  } catch (err) {
    apptNotice("Appointments could not be loaded", err.message.charAt(0).toUpperCase() + err.message.slice(1) + ".");
    $("apptList").innerHTML = "";
  } finally {
    $("apptLoading").hidden = true;
  }
}

async function setAppointmentStatus(id, status) {
  try {
    const r = await fetch(`/api/v1/voice/appointments/${id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "the appointment could not be updated");
    notify(status === "done" ? "Marked done" : status === "cancelled" ? "Visit cancelled" : "Visit reopened",
      status === "done" ? "The patient's feedback call is scheduled." : "", "good");
    recordLoadedFor = null;              // the Patient view must read the record again
    await loadAppointments();
  } catch (err) {
    notify("Could not update the visit", err.message.charAt(0).toUpperCase() + err.message.slice(1) + ".", "info");
  }
}

function openPatientRecord(query) {
  $("recordQuery").value = query;
  recordLoadedFor = null;
  switchTab("patient");
}

// ---------------------------------------------------------------- the booking sheet
function slotText(dateIso, hhmm) {   // "2026-11-03", "14:00" -> "Tuesday 3 November at 2:00 PM", the format the voicebot already uses
  const [y, m, d] = dateIso.split("-").map(Number), [h, min] = hhmm.split(":").map(Number);
  const day = new Date(Date.UTC(y, m - 1, d));
  const wd = day.toLocaleDateString("en-GB", { weekday: "long", timeZone: "UTC" });
  const mon = day.toLocaleDateString("en-GB", { month: "long", timeZone: "UTC" });
  return `${wd} ${d} ${mon} at ${h % 12 || 12}:${String(min).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

function chosenPatient() { return (window.voicebotPatients || [])[$("apptPatient").value]; }

function renderDoctorPicker() {
  $("apptDoctors").innerHTML = doctorsData.map((d, i) => `
    <button type="button" class="doc-opt ${i === apptDoctorIdx ? "is-on" : ""}" onclick="pickApptDoctor(${i})">
      <span class="avatar" style="background:${AVATAR_COLORS[i % AVATAR_COLORS.length]}">${esc(initials(d.name.replace(/^Dr\.?\s*/, "")))}</span>
      <span><b>${esc(d.name)}</b><small>${esc(d.dept)}</small></span>
    </button>`).join("");
}
function pickApptDoctor(i) { apptDoctorIdx = i; renderDoctorPicker(); renderSlotChips(); }
window.pickApptDoctor = pickApptDoctor;

function renderSlotChips() {
  const date = $("apptDate").value, doc = doctorsData[apptDoctorIdx], patient = chosenPatient();
  const keep = $("apptSlot").value;
  $("apptSlot").value = "";
  if (!date) { $("apptTimes").innerHTML = ""; return; }
  $("apptTimes").innerHTML = SLOT_TIMES.map((t) => {
    const text = slotText(date, t);
    const past = slotTextMs(text) < Date.now();
    const taken = !past && findClash(doc.name, text, patient && patient.external_id, window.apptList);
    const on = keep === text && !past && !taken;
    if (on) $("apptSlot").value = text;
    return `<button type="button" class="slot ${on ? "is-on" : ""}" ${past || taken ? "disabled" : ""} data-text="${esc(text)}"
      title="${taken ? "Already booked" : past ? "In the past" : ""}" onclick="pickApptSlot(this)">${esc(new Date(`2000-01-01T${t}`).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }))}</button>`;
  }).join("");
}
function pickApptSlot(btn) {
  $("apptSlot").value = btn.dataset.text;
  document.querySelectorAll("#apptTimes .slot").forEach((b) => b.classList.toggle("is-on", b === btn));
  apptMessage("");
}
window.pickApptSlot = pickApptSlot;

async function openApptSheet(prefill, mode) {
  if (typeof loadVoicebotPatients === "function") {
    await loadVoicebotPatients();
  }
  const patients = window.voicebotPatients || [];
  $("apptPatient").innerHTML = patients.length
    ? patients.map((p, i) => `<option value="${i}">${esc(p.name || "Unnamed")}${p.phone_e164 ? ` (${esc(p.phone_e164)})` : ""}</option>`).join("")
    : `<option value="">No registered patients</option>`;
  const today = dubaiDay(Date.now());
  const lastToday = slotTextMs(slotText(today, SLOT_TIMES[SLOT_TIMES.length - 1]));
  $("apptDate").min = today;
  $("apptDate").value = lastToday > Date.now() ? today : dubaiDay(Date.now() + 864e5);   // nothing left today: start from tomorrow
  $("apptSlot").value = "";
  $("apptReason").value = "";
  apptDoctorIdx = 0;
  apptMessage("");
  if (prefill) {
    const pi = patients.findIndex((x) => x.phone_e164 === prefill.phone);
    if (pi >= 0) $("apptPatient").value = String(pi);
    const di = doctorsData.findIndex((x) => prefill.doctor && (prefill.doctor.includes(x.name.toLowerCase().replace(/^dr\.?\s*/, "")) || x.name.toLowerCase().includes(prefill.doctor.replace(/^dr\.?\s*/, ""))));
    if (di >= 0) apptDoctorIdx = di;
    $("apptReason").value = prefill.reason || "";
    if (prefill.timing) apptMessage(`Follow-up suggested ${prefill.timing}. Pick a day and time.`);
  }
  if (!(prefill && prefill.phone)) {       // otherwise start from the patient on the care plan form
    const formPhone = ($("mobile_number")?.value || "").trim();
    const fi = patients.findIndex((x) => x.phone_e164 === formPhone);
    if (fi >= 0) $("apptPatient").value = String(fi);
  }
  renderDoctorPicker();
  renderSlotChips();
  if (mode === "call") {
    apptMessage("Select a doctor and slot, then click 'Have the assistant call to book'.");
  }
  $("apptSheet").hidden = false;
  document.body.style.overflow = "hidden";
}
function closeApptSheet() { $("apptSheet").hidden = true; document.body.style.overflow = ""; }
window.openApptSheet = openApptSheet;
window.closeApptSheet = closeApptSheet;

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("apptSheet") && !$("apptSheet").hidden) closeApptSheet();
});

function pollAppointmentCall(callId) {
  clearInterval(apptTimer);
  apptTimer = setInterval(async () => {
    try {
      const d = await (await fetch(`/api/v1/voice/calls/${callId}`)).json();
      const text = { calling: "Placing the call...", ringing: "Ringing the patient...", in_call: "On the call...", ended: "Call ended." }[d.stage];
      if (text) apptMessage(text);
      if (d.done) {
        clearInterval(apptTimer);
        notify("Call ended", d.summary || "", "info");
        closeApptSheet();
        loadAppointments();
      }
    } catch { /* keep polling */ }
  }, 4000);
}

async function submitAppointment(e, mode) {
  if (e) e.preventDefault();
  const patient = chosenPatient();
  const doc = doctorsData[apptDoctorIdx];
  if (!patient) { apptMessage("Choose a patient first.", true); return; }
  const slot = $("apptSlot").value.trim();
  if (!slot) { apptMessage("Pick a day and a time.", true); return; }
  const reason = $("apptReason").value.trim() || "Consultation";
  const calling = mode === "call";
  const clash = findClash(doc.name, slot, patient.external_id, window.apptList);
  if (clash) { apptMessage(`${clash.doctor_name} or this patient already has a visit at that time.`, true); return; }
  apptMessage(calling ? "Placing the call..." : "Booking...");
  try {
    const r = await fetch(calling ? "/api/v1/voice/book-appointment-call" : "/api/v1/voice/book-appointment-direct", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(calling
        ? { doctor_name: doc.name, doctor_title: doc.title, hospital: doc.hospital, slot, reason, patient_id: patient.external_id,
            patient_name: patient.name, phone_number: patient.phone_e164 }
        : { doctor_name: doc.name, specialty: doc.dept, booked_slot: slot, reason, patient_id: patient.external_id,
            patient_name: patient.name, phone_number: patient.phone_e164 }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "the appointment could not be booked");
    if (calling) pollAppointmentCall(d.call_id);
    else { notify("Booked", `${patient.name || "The patient"} with ${doc.name}, ${slot}.`, "good"); closeApptSheet(); await loadAppointments(); }
  } catch (err) {
    apptMessage(err.message.charAt(0).toUpperCase() + err.message.slice(1) + ".", true);
  }
}

async function openAppointmentsView() {
  if (!(window.voicebotPatients || []).length && typeof loadPatientRoster === "function") await loadPatientRoster();
  clearBookingBadge();
  await loadAppointments();
  if (window.pendingApptPrefill) { const p = window.pendingApptPrefill; window.pendingApptPrefill = null; openApptSheet(p); }
}

document.querySelectorAll("#apptFilter button").forEach((b) => b.addEventListener("click", () => { apptFilter = b.dataset.f; renderApptList(); }));
document.addEventListener("DOMContentLoaded", () => {
  $("apptSource").addEventListener("change", (e) => { apptDirection = e.target.value; loadAppointments(); });
  $("apptSearch").addEventListener("input", (e) => { apptQuery = e.target.value; renderApptList(); });
  $("apptDate").addEventListener("change", renderSlotChips);
  $("apptPatient").addEventListener("change", renderSlotChips);
});
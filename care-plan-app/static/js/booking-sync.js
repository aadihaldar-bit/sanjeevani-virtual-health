// Booking sync between the Sanjeevani app and the voice assistant. Both write to the same bookings, so this keeps the app live:
// it watches for visits booked by phone, tells the person at the desk, groups visits by day, warns about double bookings,
// and turns the care plan's "follow-up visit" into a pre-filled booking.
"use strict";

const BOOK_POLL_MS = 20000;
let bookKnown = null;     // ids seen so far; null until the first load, so opening the app never floods toasts
let bookNew = 0;
let bookTimer = null;

const BOOK_MONTH = /(\d{1,2})\s+([A-Za-z]{3,9})[,\s]+(?:at\s+)?(\d{1,2}):(\d{2})\s*(AM|PM)/i;
const dubaiDay = (ms) => new Date(ms).toLocaleDateString("en-CA", { timeZone: "Asia/Dubai" });

// When a visit is, in ms. The voicebot sends a UTC time when it has one, else free text such as "Monday 2 November at 11:00 AM".
function slotMs(a) {
  if (a.slot_start) return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(a.slot_start) ? a.slot_start : a.slot_start + "Z").getTime();
  return slotTextMs(a.booked_slot, a.created_at);
}
function slotTextMs(text, createdIso) {
  const m = BOOK_MONTH.exec(text || "");
  if (!m) return NaN;
  const year = createdIso ? new Date(createdIso + "Z").getUTCFullYear() : new Date().getFullYear();
  const ms = Date.parse(`${m[2]} ${m[1]}, ${year} ${m[3]}:${m[4]} ${m[5]} GMT+0400`);
  return Number.isNaN(ms) ? NaN : ms;
}

// Today, Tomorrow, Later, then visits with no usable time, then earlier ones, then finished ones.
function groupAppointments(list) {
  const today = dubaiDay(Date.now()), tomorrow = dubaiDay(Date.now() + 864e5);
  const g = { Today: [], Tomorrow: [], Later: [], "Time to be confirmed": [], Earlier: [], "Done or cancelled": [] };
  list.forEach((a) => {
    const t = slotMs(a);
    if (a.status === "done" || a.status === "cancelled") g["Done or cancelled"].push([t, a]);
    else if (Number.isNaN(t)) g["Time to be confirmed"].push([t, a]);
    else if (dubaiDay(t) === today) g.Today.push([t, a]);
    else if (dubaiDay(t) === tomorrow) g.Tomorrow.push([t, a]);
    else if (t > Date.now()) g.Later.push([t, a]);
    else g.Earlier.push([t, a]);
  });
  const asc = (x, y) => x[0] - y[0], desc = (x, y) => y[0] - x[0];
  ["Today", "Tomorrow", "Later"].forEach((k) => g[k].sort(asc));
  ["Earlier", "Done or cancelled"].forEach((k) => g[k].sort(desc));
  return Object.entries(g).filter(([, v]) => v.length).map(([title, v]) => ({ title, items: v.map(([, a]) => a) }));
}

// Someone already booked the same doctor, or the same patient, at that time.
function findClash(doctorName, slotText, patientExtId, existing) {
  const want = slotTextMs(slotText);
  const norm = (s) => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
  return (existing || []).find((a) => {
    if (a.status === "cancelled" || a.status === "done") return false;
    const same = !Number.isNaN(want) ? slotMs(a) === want : norm(a.booked_slot) === norm(slotText);
    return same && (norm(a.doctor_name) === norm(doctorName) || (patientExtId && a.patient_external_id === patientExtId));
  });
}

// ---------------------------------------------------------------- live updates
function setBookingBadge(n) {
  document.querySelectorAll('[data-tab="appointments"]').forEach((b) => { if (n > 0) b.dataset.new = n; else delete b.dataset.new; });
}
function clearBookingBadge() { bookNew = 0; setBookingBadge(0); }

async function pollBookings() {
  clearTimeout(bookTimer);
  bookTimer = setTimeout(pollBookings, BOOK_POLL_MS);
  if (document.hidden || (typeof VOICE_CONNECTED !== "undefined" && !VOICE_CONNECTED)) return;
  let list;
  try {
    const r = await fetch("/api/v1/voice/appointments");
    if (!r.ok) return;
    list = (await r.json()).appointments || [];
  } catch (_) { return; }

  if (bookKnown === null) { bookKnown = new Set(list.map((a) => a.id)); return; }
  const fresh = list.filter((a) => !bookKnown.has(a.id));
  if (!fresh.length) return;
  fresh.forEach((a) => bookKnown.add(a.id));

  fresh.slice(0, 3).forEach((a) => {
    const when = a.slot_start ? dubai(a.slot_start) : a.booked_slot || "time to be confirmed";
    notify(a.source === "inbound" ? "New booking from a call" : "New booking",
      `${a.patient_name || "A patient"} with ${a.doctor_name || "a doctor"}, ${when}.`, "good");
  });
  if (currentTab === "appointments") { if (typeof loadAppointments === "function") loadAppointments(); }
  else { bookNew += fresh.length; setBookingBadge(bookNew); }
  if (currentTab === "patient" && typeof loadPatientRecord === "function") { recordLoadedFor = null; loadPatientRecord().catch(() => {}); }
}
// A booking made in the app is known straight away, so it never shows up as "new".
function noteOwnBooking(id) { if (bookKnown && id != null) bookKnown.add(id); }

document.addEventListener("visibilitychange", () => { if (!document.hidden) pollBookings(); });
setTimeout(pollBookings, 4000);

// ---------------------------------------------------------------- care plan to booking
function prefillFollowUpVisit() {
  const d = latestApiResponse || {};
  const phone = (d.patient_context || {}).mobile_number;
  const revisit = ((d.ai_next_best_actions || {}).doctor_revisit_schedule) || {};
  const docName = ((d.ai_clinical_summary || {}).doctor_name || "").toLowerCase();
  window.pendingApptPrefill = {
    phone,
    doctor: docName,
    reason: revisit.purpose || (revisit.timing ? `Follow-up ${revisit.timing}` : "Follow-up visit"),
    timing: revisit.timing || "",
  };
  switchTab("appointments");
}
window.prefillFollowUpVisit = prefillFollowUpVisit;

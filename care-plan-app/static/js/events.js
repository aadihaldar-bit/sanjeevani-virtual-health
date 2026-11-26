// Live updates: asks the app's own server (a local database, so it is cheap) every few seconds whether anything happened, such as a
// call ending, and refreshes whatever is on screen. The slower polling elsewhere stays as the safety net if a webhook is missed.
"use strict";

let eventsLast = null;

function onCallEnded() {
  notify("A call just ended", "The patient's record and the lists are up to date.", "info");
  recordLoadedFor = null;
  if (currentTab === "appointments" && typeof loadAppointments === "function") loadAppointments();
  if (currentTab === "patient" && typeof loadPatientRecord === "function" && $("recordQuery")?.value) loadPatientRecord().catch(() => {});
  if (currentTab === "alerts" && typeof loadAlerts === "function") loadAlerts();
  if (currentTab === "care-journey" && typeof refreshFollowUp === "function") refreshFollowUp();
}

async function pollEvents() {
  if (!document.hidden) {
    try {
      const r = await fetch(`/api/v1/events?since=${eventsLast === null ? -1 : eventsLast}`);
      if (r.ok) {
        const d = await r.json();
        if (eventsLast !== null && d.events.some((e) => e.kind === "call_ended")) onCallEnded();
        eventsLast = d.last;
      }
    } catch (_) { /* try again next time */ }
  }
  setTimeout(pollEvents, 5000);
}
setTimeout(pollEvents, 3000);

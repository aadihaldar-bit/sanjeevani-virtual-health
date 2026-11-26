// A banner when the voice assistant is down or unwell, so empty tabs are explained instead of looking broken.
// Asks the server once a minute (the server caches its own probe for 15 s) and whenever the page becomes visible again.
"use strict";

let healthState = "ok";

function renderHealth(h) {
  healthState = h.state;
  let bar = $("voiceBanner");
  const bad = h.state === "down" || h.state === "degraded";
  if (!bad) { if (bar) bar.remove(); return; }
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "voiceBanner";
    bar.className = "notice notice-warn health-banner";
    bar.setAttribute("role", "status");
    document.querySelector("main .wrap").prepend(bar);
  }
  const text = h.state === "down"
    ? "The voice assistant is not responding. Calls, bookings and patient timelines may be out of date until it is back."
    : `The voice assistant has a problem: ${h.detail || "it is not working properly"}. Some calls and bookings may fail.`;
  bar.innerHTML = `${icon("alert")}<div><b>${h.state === "down" ? "Voice assistant offline" : "Voice assistant degraded"}</b><p>${esc(text)}</p></div>`;
}

async function checkVoiceHealth() {
  if (!document.hidden) {
    try {
      const r = await fetch("/api/v1/voice/health");
      if (r.ok) renderHealth(await r.json());
    } catch (_) {
      renderHealth({ state: "down" });          // the app's own server did not answer
    }
  }
  setTimeout(checkVoiceHealth, 60000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) fetch("/api/v1/voice/health").then((r) => r.json()).then(renderHealth).catch(() => {}); });
setTimeout(checkVoiceHealth, 2500);

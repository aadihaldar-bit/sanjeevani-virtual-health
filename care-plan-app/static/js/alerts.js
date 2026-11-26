// Alerts view: refills running out and worrying vitals estimates wait here for a person to review. Nothing is dialled by itself;
// "Call now" asks the server, which checks consent, the allow-list, quiet hours and the daily limit before the assistant rings.
"use strict";

let alertCallTimer = null;
let alertQuiet = false;
const ALERT_KIND = { refill: { icon: "pill", label: "Refill" }, vitals: { icon: "heart", label: "Vitals" } };

function alertCard(a) {
  const k = ALERT_KIND[a.kind] || { icon: "alert", label: humanise(a.kind) };
  const p = a.patient || {};
  const query = p.external_id || p.phone || "";
  const open = a.status === "queued" || a.status === "blocked";
  return `<article class="card card-pad alert ${open ? "" : "is-done"}" data-alert="${esc(a.id)}">
    <div class="alert-top">
      <span class="tl-dot">${icon(k.icon)}</span>
      <div class="alert-main">
        <div class="tl-head"><b>${esc(a.title)}</b>${chipFor(a.status === "called" ? "done" : a.status === "dismissed" ? "" : a.status === "blocked" ? "blocked" : "pending")}
          <time>${esc(dubai(a.created_at))}</time></div>
        <p>${esc(a.detail)}</p>
        <small>${query ? `<a href="#patient" onclick="openPatientRecord('${esc(query)}');return false">${esc(p.name || "Patient")}</a>` : esc(p.name || "Patient")}${p.phone ? ` · ${esc(p.phone)}` : ""}</small>
        ${a.status === "blocked" && a.reason ? `<p class="fu-flag">${icon("alert")}Not called: ${esc(a.reason)}.</p>` : ""}
        ${a.status === "called" ? `<p class="small muted alert-progress" id="alertProg-${esc(a.id)}">The care assistant was asked to call.</p>` : ""}
      </div>
    </div>
    ${open ? `<div class="alert-actions">
      <button class="btn btn-primary btn-sm" ${alertQuiet ? "disabled title=\"Quiet hours in Dubai\"" : ""} onclick="callAlert('${esc(a.id)}')">${icon("phone")}Call now</button>
      <button class="btn btn-secondary btn-sm" onclick="dismissAlert('${esc(a.id)}')">Dismiss</button>
    </div>` : ""}
  </article>`;
}

async function loadAlerts() {
  $("alertLoading").hidden = false;
  try {
    const r = await fetch("/api/v1/alerts");
    const d = await r.json();
    const rules = d.rules || {};
    alertQuiet = !!rules.quiet_now;
    $("alertRules").textContent = `Calls only go to patients who agreed to them, from ${rules.quiet_hours || "08:00 to 21:00 Dubai"}, at most ${rules.max_calls_per_day || 2} a day each.`
      + (rules.quiet_now ? " It is quiet hours now, so Call now is paused." : "");
    $("alertNotConnected").hidden = rules.connected !== false;
    const list = d.alerts || [];
    const queued = list.filter((a) => a.status === "queued" || a.status === "blocked");
    const handled = list.filter((a) => !(a.status === "queued" || a.status === "blocked") && a.status !== "dismissed").slice(0, 10);
    $("alertList").innerHTML = (queued.length ? `<h3 class="tl-day">Needs a call</h3>${queued.map(alertCard).join("")}`
      : `<div class="empty"><b>Nothing waiting</b>Alerts appear here when a refill is running out or a vitals check looks worth a call.</div>`)
      + (handled.length ? `<h3 class="tl-day">Handled</h3>${handled.map(alertCard).join("")}` : "");
    hydrateIcons($("alertList"));
    setAlertBadge(queued.length);
  } catch (_) {
    $("alertList").innerHTML = `<div class="empty"><b>Could not load alerts</b>Try again in a moment.</div>`;
  } finally {
    $("alertLoading").hidden = true;
  }
}

function setAlertBadge(n) {
  document.querySelectorAll('[data-tab="alerts"]').forEach((b) => { if (n > 0) b.dataset.new = n; else delete b.dataset.new; });
}

async function scanAlerts() {
  const btn = $("alertScan");
  btn.disabled = true; btn.textContent = "Checking...";
  try {
    const r = await fetch("/api/v1/alerts/scan", { method: "POST" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "the check failed");
    notify("Refills checked", `${d.checked} patients checked, ${d.created} new ${d.created === 1 ? "alert" : "alerts"}${d.skipped_no_consent ? `, ${d.skipped_no_consent} skipped (no call consent)` : ""}.`, "info");
    await loadAlerts();
  } catch (e) {
    notify("Could not check refills", e.message, "info");
  } finally {
    btn.disabled = false; btn.textContent = "Check refills";
  }
}

async function callAlert(id) {
  const a = [...document.querySelectorAll("[data-alert]")].find((c) => c.dataset.alert === id);
  if (!confirm("Ask the care assistant to phone this patient now?")) return;
  try {
    const r = await fetch(`/api/v1/alerts/${id}/call`, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (r.status === 409) { notify("Not called", (d.detail || "a safety check stopped it").replace(/^./, (c) => c.toUpperCase()) + ".", "info"); await loadAlerts(); return; }
    if (!r.ok) throw new Error(d.detail || "the call could not be placed");
    notify("Calling", `The care assistant is ringing ${d.phone_number}.`, "good");
    await loadAlerts();
    watchAlertCall(id, d.call_id);
  } catch (e) {
    notify("The call could not be placed", e.message.charAt(0).toUpperCase() + e.message.slice(1) + ".", "info");
  }
}

function watchAlertCall(alertId, callId) {
  clearInterval(alertCallTimer);
  alertCallTimer = setInterval(async () => {
    const el = $(`alertProg-${alertId}`);
    try {
      const d = await (await fetch(`/api/v1/voice/calls/${callId}`)).json();
      const text = { calling: "Placing the call...", ringing: "Ringing the patient...", in_call: "On the call...", ended: "Call ended." }[d.stage];
      if (el && text) el.textContent = d.done && d.summary ? `Call ended: ${d.summary}` : text;
      if (d.done) clearInterval(alertCallTimer);
    } catch (_) { /* keep polling */ }
  }, 4000);
}

async function dismissAlert(id) {
  await fetch(`/api/v1/alerts/${id}/dismiss`, { method: "POST" });
  loadAlerts();
}

function openAlertsView() { return loadAlerts(); }

// Vitals: a well-read estimate far from usual queues an alert for the patient on the form. A person still decides about the call.
async function reportVitalsForAlerts(result) {
  try {
    const h = result.heart || {};
    const br = (result.cards || []).find((c) => /breath/i.test(c.title) && typeof c.value === "number") || {};
    const body = {
      patient_id: ($("patient_id")?.value || "").trim() || null, phone: ($("mobile_number")?.value || "").trim() || null,
      name: ($("patient_name")?.value || "").trim() || null,
      hr: typeof h.value === "number" ? h.value : null, hr_confidence: h.confidence || null,
      rr: typeof br.value === "number" ? br.value : null, rr_confidence: br.confidence || null,
    };
    const r = await fetch("/api/v1/alerts/vitals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.alert) notify("An alert was queued", "This check looked worth a call. A person reviews it in Alerts.", "info");
  } catch (_) { /* the vitals result is already on screen; an alert is a bonus */ }
}
window.reportVitalsForAlerts = reportVitalsForAlerts;

// The tab badge counts alerts waiting, refreshed once a minute while the page is open.
async function pollAlertBadge() {
  if (!document.hidden) {
    try {
      const d = await (await fetch("/api/v1/alerts")).json();
      setAlertBadge((d.alerts || []).filter((a) => a.status === "queued" || a.status === "blocked").length);
    } catch (_) { /* ignore */ }
  }
  setTimeout(pollAlertBadge, 60000);
}
setTimeout(pollAlertBadge, 5000);

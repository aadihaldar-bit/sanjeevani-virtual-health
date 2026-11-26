// Follow-up loop on the care plan: shows the calls the voice assistant makes after a plan is created (explaining the medicines,
// then checking in) and what the patient said, read back from the voicebot's patient record.
"use strict";

let followupTimer = null;
let followupPlan = null;

const FU_STEP = {
  explain: { title: "Explains the medicines", sub: "A call to walk through each dose" },
  feedback: { title: "Checks in", sub: "Asks if the medicines were taken and how they helped" },
};
const FU_WORD = { yes: "Taken as prescribed", mostly: "Mostly taken", partly: "Partly taken", no: "Not taken", none: "Not taken" };

const fuWhen = (iso) => (typeof dubai === "function" ? dubai(iso) : iso);

// Adherence from what the patient said: all "taken" answers count, "mostly" and "partly" count in part.
function fuAdherence(feedback) {
  const score = { yes: 1, mostly: 0.75, partly: 0.4, no: 0, none: 0 };
  const known = feedback.map((f) => score[String(f.taken || "").toLowerCase()]).filter((s) => s !== undefined);
  return known.length ? Math.round((known.reduce((a, b) => a + b, 0) / known.length) * 100) : null;
}

function fuStopPolling() { if (followupTimer) clearTimeout(followupTimer); followupTimer = null; }

async function fuLoad() {
  if (!followupPlan) return;
  const { prescriptionId, phone } = followupPlan;
  let d;
  try {
    const r = await fetch(`/api/v1/voice/patient-record?phone=${encodeURIComponent(phone)}`);
    if (!r.ok) throw new Error();
    d = await r.json();
  } catch (_) {
    $("fuBody").innerHTML = `<p class="small muted">Could not read the follow-up from the voice assistant just now.</p>`;
    followupTimer = setTimeout(fuLoad, 30000);
    return;
  }
  if (!followupPlan || followupPlan.prescriptionId !== prescriptionId) return;

  const rx = (d.prescriptions || []).find((p) => p.external_id === prescriptionId);
  const tasks = rx ? (d.follow_up_calls || []).filter((t) => t.prescription_id === rx.id).sort((a, b) => (a.due_at > b.due_at ? 1 : -1)) : [];
  const since = rx ? new Date(/[zZ]$/.test(rx.issued_at) ? rx.issued_at : rx.issued_at + "Z").getTime() : 0;
  // Newer voicebots say which prescription an answer is about; older ones do not, so fall back to "after it was issued".
  const fbAll = d.feedback || [];
  const feedback = rx && fbAll.some((f) => f.prescription_id !== undefined)
    ? fbAll.filter((f) => f.prescription_id === rx.id)
    : fbAll.filter((f) => new Date(/[zZ]$/.test(f.created_at) ? f.created_at : f.created_at + "Z").getTime() >= since);

  if (!rx || !tasks.length) {
    $("fuBody").innerHTML = `<p class="small muted">The voice assistant has not booked the calls yet. This usually takes a few seconds.</p>`;
    followupTimer = setTimeout(fuLoad, 8000);
    return;
  }

  const steps = tasks.map((t) => {
    const meta = FU_STEP[t.kind] || { title: humanise(t.kind), sub: "" };
    const done = t.status === "done" || t.status === "completed";
    return `<li class="fu-step ${done ? "is-done" : ""}">
      <span class="fu-dot">${done ? icon("check") : ""}</span>
      <div><b>${esc(meta.title)}</b><span>${esc(fuWhen(t.due_at))}${t.attempts ? ` · ${t.attempts} ${t.attempts === 1 ? "try" : "tries"}` : ""}</span></div>
      ${chipFor(t.status)}
    </li>`;
  }).join("");

  const pct = fuAdherence(feedback);
  const results = feedback.length ? feedback.map((f) => `
      <div class="fu-result">
        <div class="fu-result-top"><b>${esc(FU_WORD[String(f.taken || "").toLowerCase()] || humanise(f.taken) || "Checked in")}</b>
          ${f.satisfaction ? `<span class="small muted">${"★".repeat(f.satisfaction)}${"☆".repeat(5 - f.satisfaction)}</span>` : ""}
          <time>${esc(fuWhen(f.created_at))}</time></div>
        ${f.helped ? `<p>Helped: ${esc(humanise(f.helped))}</p>` : ""}
        ${f.side_effects ? `<p class="fu-flag">${icon("alert")}Side effects: ${esc(f.side_effects)}</p>` : ""}
        <div class="fu-actions">
          ${f.wants_doctor ? `<button type="button" class="btn btn-secondary btn-sm" onclick="switchTab('doctors')">${icon("stethoscope")}They want a doctor: book a visit</button>` : ""}
          ${f.refill_requested ? `<button type="button" class="btn btn-secondary btn-sm" onclick="switchTab('pharmacy')">${icon("truck")}They asked for a refill</button>` : ""}
        </div>
      </div>`).join("") : `<p class="small muted fu-wait">Nothing back yet. What the patient says on the calls will show here.</p>`;

  $("fuBody").innerHTML = `
    <ol class="fu-steps">${steps}</ol>
    ${pct !== null ? `<div class="fu-adh"><div><b class="num">${pct}%</b><span>taking it as prescribed</span></div><div class="fu-bar"><i style="width:${pct}%"></i></div></div>` : ""}
    ${results}`;

  const open = tasks.some((t) => !["done", "completed", "cancelled", "failed"].includes(t.status));
  if (open && currentTab === "care-journey") followupTimer = setTimeout(fuLoad, 30000);
}

function renderFollowUp(data) {
  fuStopPolling();
  followupPlan = null;
  const card = $("followupCard");
  if (!card) return;
  const f = (data.voice_bot_integration || {}).follow_up_calls || {};
  const phone = f.phone_number || (data.patient_context || {}).mobile_number;
  const waiting = f.status === "scheduled" || f.status === "exists";
  card.hidden = false;
  if (!waiting) {
    const why = f.status === "not_requested" ? "The patient has not agreed to calls, so none are booked. Tick the box on the form to turn them on."
      : f.status === "not_allowed" ? "Calls from this preview only go to the demo numbers."
      : f.status === "failed" ? "The calls could not be booked just now."
      : "Connect the voice assistant to book follow-up calls.";
    $("fuBody").innerHTML = `<p class="small muted">${esc(why)}</p>`;
    return;
  }
  followupPlan = { prescriptionId: data.prescription_id, phone };
  $("fuBody").innerHTML = `<div class="sk" style="height:14px;width:60%"></div><div class="sk" style="height:14px;width:40%;margin-top:10px"></div>`;
  fuLoad();
}
window.renderFollowUp = renderFollowUp;
window.refreshFollowUp = () => { fuStopPolling(); fuLoad(); };

// Live numbers for the Insights tab, worked out from what the voice assistant holds: patients, calls, visits, orders and what
// patients said on feedback calls. The older tiles and charts below them are sample data and are labelled so.
"use strict";

let liveChart = null;

const lvDay = (ms) => new Date(ms).toLocaleDateString("en-CA", { timeZone: "Asia/Dubai" });
const lvMs = (iso) => (iso ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z").getTime() : NaN);
const lvPct = (n, of) => (of ? `${Math.round((n / of) * 100)}%` : "–");

async function lvJson(url) {
  try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch (_) { return null; }
}

async function loadLiveInsights() {
  const box = $("liveInsights");
  if (!box) return;
  const list = await lvJson("/api/v1/voice/patients");
  if (!list) { box.hidden = true; return; }          // no voice assistant connected: only the sample data shows
  box.hidden = false;
  if (!$("liveKpis").children.length) $("liveKpis").innerHTML = Array.from({ length: 8 }, () => `<div class="kpi"><div class="sk" style="height:12px;width:55%"></div><div class="sk" style="height:30px;width:40%;margin-top:12px"></div><div class="sk" style="height:10px;width:70%;margin-top:12px"></div></div>`).join("");
  const patients = list.patients || [];
  const [records, appts, alerts] = await Promise.all([
    Promise.all(patients.map((p) => lvJson(`/api/v1/voice/patient-record?${p.external_id ? `patient_id=${encodeURIComponent(p.external_id)}` : `phone=${encodeURIComponent(p.phone_e164)}`}`))),
    lvJson("/api/v1/voice/appointments"), lvJson("/api/v1/alerts"),
  ]);

  const calls = new Map(), orders = new Map(), feedback = new Map();
  records.filter(Boolean).forEach((rec) => {
    (rec.calls || []).forEach((c) => calls.set(c.id, c));
    (rec.orders || []).forEach((o) => orders.set(o.reference || o.id, o));
    (rec.feedback || []).forEach((f) => feedback.set(`${f.created_at}|${f.satisfaction}`, f));
  });
  const callList = [...calls.values()], answered = callList.filter((c) => c.duration_seconds > 0);
  const inbound = callList.filter((c) => c.direction === "inbound").length;
  const orderList = [...orders.values()], revenue = orderList.reduce((s, o) => s + (Number(o.amount) || 0), 0);
  const fb = [...feedback.values()];
  const rated = fb.filter((f) => f.satisfaction), taken = fb.filter((f) => f.taken);
  const score = { yes: 1, mostly: 0.75, partly: 0.4, no: 0, none: 0 };
  const adherence = taken.length ? Math.round((taken.reduce((s, f) => s + (score[String(f.taken).toLowerCase()] ?? 0), 0) / taken.length) * 100) : null;
  const avg = rated.length ? (rated.reduce((s, f) => s + f.satisfaction, 0) / rated.length).toFixed(1) : null;
  const visits = (appts && appts.appointments) || [];
  const open = visits.filter((a) => a.status !== "done" && a.status !== "cancelled").length;
  const waiting = ((alerts && alerts.alerts) || []).filter((a) => a.status === "queued" || a.status === "blocked").length;

  const kpi = (label, value, sub) => `<div class="kpi"><span>${label}</span><b>${value}</b><small>${sub}</small></div>`;
  $("liveKpis").innerHTML = [
    kpi("Patients", patients.length, `${patients.filter((p) => p.consent_calls).length} agreed to calls`),
    kpi("Calls", callList.length, `${inbound} from patients · ${callList.length - inbound} from the assistant`),
    kpi("Calls answered", lvPct(answered.length, callList.length), `${answered.length} of ${callList.length} reached a person`),
    kpi("Visits", visits.length, `${open} coming up · ${visits.filter((a) => a.status === "done").length} done`),
    kpi("Pharmacy orders", orderList.length, orderList.length ? `AED ${Math.round(revenue).toLocaleString("en-GB")} in total` : "None placed by phone yet"),
    kpi("Taking medicines as prescribed", adherence === null ? "–" : `${adherence}%`, adherence === null ? "No feedback calls answered yet" : `from ${taken.length} ${taken.length === 1 ? "answer" : "answers"}`),
    kpi("Patient rating", avg === null ? "–" : `${avg}<span class="kpi-of"> / 5</span>`, avg === null ? "No ratings yet" : `from ${rated.length} ${rated.length === 1 ? "rating" : "ratings"}`),
    kpi("Alerts waiting", waiting, waiting ? "A person decides about each" : "Nothing needs a call"),
  ].join("");

  // Calls per day over the past week, answered and not.
  const days = [];
  for (let i = 6; i >= 0; i--) days.push(lvDay(Date.now() - i * 864e5));
  const byDay = (pred) => days.map((d) => callList.filter((c) => pred(c) && lvDay(lvMs(c.started_at)) === d).length);
  if (!window.Chart) return;
  if (liveChart) liveChart.destroy();
  liveChart = new Chart($("chartLiveCalls"), {
    type: "bar",
    data: {
      labels: days.map((d) => new Date(d + "T12:00:00Z").toLocaleDateString("en-GB", { weekday: "short", day: "numeric" })),
      datasets: [
        { label: "Answered", data: byDay((c) => c.duration_seconds > 0), backgroundColor: "#1d1d1f", borderRadius: 6, stack: "c" },
        { label: "No answer", data: byDay((c) => !(c.duration_seconds > 0)), backgroundColor: "#d2d2d7", borderRadius: 6, stack: "c" },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true } } },
      scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}
window.loadLiveInsights = loadLiveInsights;

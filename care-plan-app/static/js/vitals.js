// Vitals check: record from the camera or upload a video, send it to /api/v1/vitals, poll the job, show the readings.
"use strict";

const V_MIN_SECONDS = 20;
let vStream = null, vRecorder = null, vChunks = [], vRecTimer = null, vLightTimer = null, vJob = null, vPoll = null;

const V_CONF = {
  good: ["Good", "check"],
  approximate: ["Approximate", "wave"],
  low: ["Low confidence", "alert"],
  none: ["Not measured", "minus"],
};
const vBadge = (c) => `<span class="conf conf-${c}">${icon(V_CONF[c][1])}${V_CONF[c][0]}</span>`;
const vClock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function vShow(name) {
  ["vStart", "vWork", "vError", "vResults"].forEach((id) => { $(id).hidden = id !== name; });
}

// ---------------------------------------------------------------- camera
async function vCameraOn() {
  try {
    vStream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 }, facingMode: "user" }, audio: false,
    });
  } catch (e) {
    $("vCamText").textContent = e.name === "NotAllowedError"
      ? "Camera access was blocked. Allow it from the address bar, or upload a video instead."
      : "No camera was found. You can upload a video instead.";
    return;
  }
  const v = $("vPreview");
  v.srcObject = vStream;
  await v.play();
  $("vCam").classList.add("live");
  $("vLight").hidden = false;
  $("vRecBtn").disabled = false;
  vLightTimer = setInterval(vCheckLight, 600);
}

// Brightness of the middle of the picture, where the face should be (same scale as the server's check).
const vCanvas = document.createElement("canvas");
function vCheckLight() {
  const v = $("vPreview");
  if (!v.videoWidth) return;
  vCanvas.width = 64; vCanvas.height = 48;
  const g = vCanvas.getContext("2d", { willReadFrequently: true });
  g.drawImage(v, v.videoWidth * .3, v.videoHeight * .2, v.videoWidth * .4, v.videoHeight * .55, 0, 0, 64, 48);
  const d = g.getImageData(0, 0, 64, 48).data;
  let sum = 0;
  for (let i = 0; i < d.length; i += 4) sum += (d[i] + d[i + 1] + d[i + 2]) / 3;
  const b = sum / (d.length / 4);
  const [word, color] = b < 60 ? ["too dark", "var(--bad)"] : b < 90 ? ["dim", "var(--warn)"] : b > 225 ? ["too bright", "var(--warn)"] : ["good", "var(--good)"];
  $("vLightVal").textContent = word;
  $("vLightDot").style.background = color;
}

function vPickMime() {
  const options = ["video/mp4;codecs=avc1", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm", "video/mp4"];
  return options.find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || "";
}

async function vStartRecording() {
  const seconds = Number($("vLength").value);
  $("vRecBtn").hidden = true;
  $("vLength").disabled = true;
  const c = $("vCount");
  c.hidden = false;
  for (const n of [3, 2, 1]) { c.textContent = n; await new Promise((r) => setTimeout(r, 900)); }
  c.hidden = true;

  vChunks = [];
  const mime = vPickMime();
  // 5 Mb/s keeps the skin-colour changes that carry the pulse (the model was trained on phone-compressed video) while
  // 90 s stays near 56 MB, under the 100 MB a shared link accepts.
  vRecorder = new MediaRecorder(vStream, { mimeType: mime || undefined, videoBitsPerSecond: 5_000_000 });
  vRecorder.ondataavailable = (e) => e.data.size && vChunks.push(e.data);
  vRecorder.onstop = () => vFinishRecording(mime);
  vRecorder.start(1000);
  const t0 = performance.now();
  $("vRec").hidden = $("vBar").hidden = $("vStopBtn").hidden = false;
  vRecTimer = setInterval(() => {
    const s = (performance.now() - t0) / 1000;
    $("vRecTime").textContent = `${vClock(s)} / ${vClock(seconds)}`;
    $("vFill").style.width = `${Math.min(100, (100 * s) / seconds)}%`;
    $("vStopBtn").disabled = s < V_MIN_SECONDS;
    $("vStopBtn").title = s < V_MIN_SECONDS ? `At least ${V_MIN_SECONDS} seconds are needed` : "";
    if (s >= seconds && vRecorder.state === "recording") vRecorder.stop();
  }, 250);
}

function vFinishRecording(mime) {
  clearInterval(vRecTimer);
  $("vRec").hidden = $("vBar").hidden = $("vStopBtn").hidden = true;
  $("vRecBtn").hidden = false;
  $("vLength").disabled = false;
  const type = (mime || "video/webm").split(";")[0];
  vUpload(new File(vChunks, `recording.${type.includes("mp4") ? "mp4" : "webm"}`, { type }));
}

// ---------------------------------------------------------------- upload and progress
// A shared link goes through Cloudflare's tunnel, which refuses uploads over 100 MB with an HTML page of its own.
const V_LINK_MAX_BYTES = 95e6;
const V_ON_LOCAL = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);

// The server's JSON answer, or a plain reason when something else answered (the tunnel, a restarting server).
async function vAnswer(r) {
  const text = await r.text();
  try {
    const j = JSON.parse(text);
    if (!r.ok) throw Object.assign(new Error(j.detail || j.error || "the server refused the request"), { status: r.status });
    return j;
  } catch (e) {
    if (e.status) throw e;
    if (r.status === 413) throw new Error("the video is too large to send over this link (100 MB at most); record in the app or send a shorter clip");
    if (r.status === 401) throw new Error("you are signed out; reload the page and enter the password again");
    if ([502, 503, 504, 520, 521, 522, 523, 524, 530].includes(r.status) || r.status >= 500)
      throw Object.assign(new Error("the server did not answer (it may have just restarted); please try again in a moment"), { retry: true });
    throw new Error(`the server sent an unexpected answer (HTTP ${r.status})`);
  }
}

async function vUpload(file) {
  if (!V_ON_LOCAL && file.size > V_LINK_MAX_BYTES) {
    vFail(`this video is ${(file.size / 1e6).toFixed(0)} MB, and the shared link takes at most 100 MB. Record it here in the app, ` +
          "or send a shorter or lower-resolution clip");
    return;
  }
  vShow("vWork");
  $("vWorkTitle").textContent = "Sending your video";
  $("vWorkSub").textContent = `0 of ${(file.size / 1e6).toFixed(1)} MB`;
  $("vSteps").innerHTML = "";
  $("vElapsed").textContent = "";
  $("vSendBar").hidden = false;
  $("vSendFill").style.width = "0%";
  const body = new FormData();
  body.append("video", file);
  try {
    const j = await vAnswer(await vSend("/api/v1/vitals/analyze", body, file.size));
    vJob = j.id;
    vPollMisses = 0;
    $("vSendBar").hidden = true;
    $("vWorkTitle").textContent = "Reading your video";
    $("vWorkSub").textContent = "This takes one to three minutes. You can leave this page open.";
    vPollJob();
  } catch (e) {
    $("vSendBar").hidden = true;
    vFail(e.message || "the video could not be sent");
  }
}

// POST with upload progress (fetch cannot report it): "34 of 89 MB, 1.9 MB/s, about 30 s left". If nothing moves for
// 60 s the upload is abandoned with a reason instead of leaving the page spinning.
const V_STALL_MS = 60000;
function vSend(url, body, total) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const t0 = performance.now();
    let lastMove = t0, stall = null;
    const arm = () => { clearTimeout(stall); stall = setTimeout(() => { xhr.abort(); reject(new Error(
      "the upload stopped moving. If this is a shared link, your connection's upload speed may be too low for a video this size; " +
      `on the computer running the app, open it at http://localhost:${location.port || 8030} instead`)); }, V_STALL_MS); };
    arm();
    xhr.upload.onprogress = (e) => {
      const now = performance.now();
      lastMove = now;
      arm();
      const sent = e.lengthComputable ? e.loaded : 0;
      const rate = sent / Math.max((now - t0) / 1000, 0.5);                 // bytes per second since the start
      const left = rate > 0 ? Math.max(0, (total - sent) / rate) : null;
      $("vSendFill").style.width = `${Math.min(100, (100 * sent) / total)}%`;
      $("vWorkSub").textContent = `${(sent / 1e6).toFixed(1)} of ${(total / 1e6).toFixed(1)} MB` +
        (rate > 0 ? ` · ${(rate / 1e6).toFixed(1)} MB/s` : "") +
        (left !== null && sent < total ? ` · about ${left < 90 ? `${Math.ceil(left)} s` : `${Math.ceil(left / 60)} min`} left` : "");
      if (sent >= total) $("vWorkSub").textContent = "Sent. Waiting for the server to take it...";
    };
    xhr.onload = () => { clearTimeout(stall); resolve(new Response(xhr.responseText, { status: xhr.status || 599 })); };
    xhr.onerror = () => { clearTimeout(stall); reject(new Error("the connection dropped while sending the video; please try again")); };
    xhr.open("POST", url);
    xhr.send(body);
  });
}

let vPollMisses = 0;
const V_POLL_MAX_MISSES = 6;          // about 20 s of a dropped connection before giving up

async function vPollJob() {
  clearTimeout(vPoll);
  let j;
  try {
    let r;
    try {
      r = await fetch(`/api/v1/vitals/jobs/${vJob}`);
    } catch (_) {
      throw Object.assign(new Error("lost contact with the server"), { retry: true });
    }
    j = await vAnswer(r);
    vPollMisses = 0;
  } catch (e) {
    if (e.retry && ++vPollMisses <= V_POLL_MAX_MISSES) { vPoll = setTimeout(vPollJob, 3000); return; }
    return vFail(e.status === 404 ? "the server restarted and lost this video's place; please send it again" : e.message || "lost contact with the server");
  }
  if (j.status === "done") return vShowResults(j.result);
  if (j.status === "error") return vFail(j.error);
  if (j.status === "queued") {
    $("vWorkTitle").textContent = "Waiting for a free slot";
    $("vWorkSub").textContent = j.queue_position === 1 ? "One other video is being read first." : `${j.queue_position} videos are ahead of yours.`;
  }
  $("vSteps").innerHTML = j.steps.map((s, i) => `<li class="${i === j.steps.length - 1 ? "now" : ""}">${esc(s)}</li>`).join("");
  $("vElapsed").textContent = j.elapsed_s ? `${vClock(j.elapsed_s)} so far` : "";
  vPoll = setTimeout(vPollJob, 1000);
}

function vFail(msg) {
  $("vErrorMsg").textContent = msg.charAt(0).toUpperCase() + msg.slice(1) + ".";
  vShow("vError");
}

// ---------------------------------------------------------------- results
const V_ICON = { breathing: "wind", pattern: "wave", hrv: "pulse", rhythm: "gauge", strength: "drop", eyes: "eye", stress: "leaf", spo2: "drop" };

function vDetails(c) {
  if (!c.warnings.length && !c.note) return "";
  return `<details><summary>${c.warnings.length ? "Why this confidence" : "About this reading"}</summary>
    ${c.warnings.length ? `<ul>${c.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    ${c.note ? `<p>${esc(c.note)}</p>` : ""}</details>`;
}

function vCard(c) {
  const numeric = typeof c.value === "number" || /^[\d.]+$/.test(String(c.value ?? ""));
  const value = c.value == null
    ? `<div class="vval none">${esc(c.summary)}</div>`
    : `<div class="vval ${numeric ? "" : "word"}">${esc(c.value)}${c.unit ? `<small>${esc(c.unit)}</small>` : ""}</div><p class="vsum">${esc(c.summary)}</p>`;
  const sub = c.sub ? `<div class="vsub"><span>${esc(c.sub.label)}</span><span>${esc(c.sub.value)}</span></div>` : "";
  return `<article class="vcard"><div class="vcard-top"><h3>${esc(c.title)}</h3>${vBadge(c.confidence)}</div>${value}${sub}${vDetails(c)}</article>`;
}

function vShowResults(r) {
  $("vDisclaimer").textContent = r.disclaimer;
  const h = r.heart;
  $("vHero").innerHTML = h.value == null
    ? `<div class="txt"><h2>Heart rate</h2><div class="vval none">${esc(h.summary)}</div></div>`
    : `<svg class="heart" viewBox="0 0 24 24" style="--beat:${(60 / h.value).toFixed(2)}s" aria-hidden="true"><path d="M12 21s-7.5-4.6-9.6-9.3C.9 8.2 3 4.5 6.6 4.5c2.1 0 3.6 1.2 5.4 3.2 1.8-2 3.3-3.2 5.4-3.2 3.6 0 5.7 3.7 4.2 7.2C19.5 16.4 12 21 12 21z"/></svg>
       <div class="bpm">${esc(h.value)}<small>bpm</small></div>
       <div class="txt"><h2>Heart rate ${vBadge(h.confidence)}</h2><p>${esc(h.summary)}</p><div class="vcard" style="border:0;padding:0;min-height:0">${vDetails(h)}</div></div>`;
  $("vCards").innerHTML = r.cards.map(vCard).join("");
  $("vQuality").innerHTML = r.quality.map((q) => `<div><dt>${esc(q.label)}</dt><dd>${esc(q.value)}</dd></div>`).join("");
  vShow("vResults");
  if (typeof reportVitalsForAlerts === "function") reportVitalsForAlerts(r);
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (vJob) fetch(`/api/v1/vitals/jobs/${vJob}`, { method: "DELETE" });   // the server forgets it; only this page keeps it
}

function vReset() {
  vJob = null;
  vShow("vStart");
}

// ---------------------------------------------------------------- wiring
document.addEventListener("DOMContentLoaded", () => {
  $("vCamOn").addEventListener("click", vCameraOn);
  $("vRecBtn").addEventListener("click", vStartRecording);
  $("vStopBtn").addEventListener("click", () => vRecorder && vRecorder.state === "recording" && vRecorder.stop());
  const drop = $("vDrop"), file = $("vFile");
  file.addEventListener("change", () => { if (file.files[0]) { vUpload(file.files[0]); file.value = ""; } });
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); } });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && vUpload(e.dataTransfer.files[0]));
  if (!navigator.mediaDevices?.getUserMedia) {
    $("vCamText").textContent = "This browser can only use the camera over https or on localhost. Upload a video instead.";
    $("vCamOn").hidden = true;
  }
});

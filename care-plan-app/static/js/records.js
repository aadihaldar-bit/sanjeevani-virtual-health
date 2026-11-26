// Patient records on the care plan page: several prescription files at once, the documents already on file (chosen as history), a
// document viewer, the stored-documents vault on the Patient tab, past encounters, and nutrition advice from lab results.
// Brought over from dev (Sonika's work) and rebuilt on this page's design. Buttons carry an index, never a file name, so a file called
// anything at all cannot reach a script.
"use strict";

let selectedPrescriptionFiles = [];
let onFileDocs = [];                 // documents already stored for this patient
let onFileUse = new Set();           // which of them to read as history
let docZoom = 1;

const kb = (n) => `${Math.max(1, Math.round(n / 1024))} KB`;
const safeUrl = (u) => (typeof u === "string" && u.startsWith("/api/v1/patient-records/") ? u : "");

// ---------------------------------------------------------------- prescription files (several at once)
function handlePrescriptionFilesSelected(input) {
  Array.from(input.files || []).forEach((f) => {
    if (!selectedPrescriptionFiles.some((x) => x.name === f.name && x.size === f.size)) selectedPrescriptionFiles.push(f);
  });
  input.value = "";
  renderPrescriptionFiles();
}

function renderPrescriptionFiles() {
  const box = $("prescriptionFilesDisplay");
  box.hidden = !selectedPrescriptionFiles.length;
  box.innerHTML = selectedPrescriptionFiles.map((f, i) => `
    <span class="file-chip">${icon(f.type.startsWith("image/") ? "camera" : "doc")}<span>${esc(f.name)}</span><small>${kb(f.size)}</small>
      <button type="button" data-act="view" data-i="${i}" aria-label="Preview">${icon("eye")}</button>
      <button type="button" data-act="remove" data-i="${i}" aria-label="Remove">${icon("x")}</button></span>`).join("");
  $("rxFileName").textContent = selectedPrescriptionFiles.length
    ? `${selectedPrescriptionFiles.length} ${selectedPrescriptionFiles.length === 1 ? "file" : "files"} chosen. Add more`
    : "Choose photos, scans or PDFs";
  $("rxDrop").classList.toggle("has-file", !!selectedPrescriptionFiles.length);
}

// ---------------------------------------------------------------- the document viewer
function openDocViewer(url, title, subtitle) {
  const isPdf = /\.pdf($|\?)/i.test(url) || /\.pdf$/i.test(title || "");
  $("docViewerTitle").textContent = title || "Document";
  $("docViewerSub").textContent = subtitle || "";
  $("docOpen").href = url;
  docZoom = 1;
  $("docZoom").textContent = "100%";
  const img = $("docImg"), frame = $("docFrame");
  img.style.transform = "scale(1)";
  img.hidden = isPdf; frame.hidden = !isPdf;
  if (isPdf) { frame.src = url; img.removeAttribute("src"); } else { img.src = url; frame.removeAttribute("src"); }
  $("docViewer").classList.remove("hidden");
}

function closeDocViewer() {
  $("docViewer").classList.add("hidden");
  $("docImg").removeAttribute("src");
  $("docFrame").removeAttribute("src");
}

function zoomDoc(delta) {
  docZoom = delta === 0 ? 1 : Math.min(3, Math.max(0.4, docZoom + delta));
  $("docImg").style.transform = `scale(${docZoom.toFixed(2)})`;
  $("docZoom").textContent = `${Math.round(docZoom * 100)}%`;
}

// ---------------------------------------------------------------- documents already on file, offered as history
function renderOnFileDocs(docs, patientId) {
  onFileDocs = (docs || []).filter((d) => safeUrl(d.view_url));
  onFileUse = new Set(onFileDocs.map((d) => d.filename));                 // all of them, until the person unticks some
  const box = $("onFileDocs");
  box.hidden = !onFileDocs.length;
  $("onFileHint").hidden = !onFileDocs.length;
  box.innerHTML = onFileDocs.map((d, i) => `
    <span class="file-chip"><input type="checkbox" checked data-act="use" data-i="${i}" aria-label="Use ${esc(d.filename)} as history">
      <span>${esc(d.filename)}</span><small>${d.size_kb || 0} KB</small>
      <button type="button" data-act="view" data-i="${i}" aria-label="Preview">${icon("eye")}</button></span>`).join("");
  box.dataset.patient = patientId || "";
}

// What to send as included_history_documents: the ticked names, or null when nothing is on file (the server then uses its default).
function includedHistoryDocuments() {
  return onFileDocs.length ? JSON.stringify([...onFileUse]) : null;
}

// ---------------------------------------------------------------- stored documents on the Patient tab
async function loadPatientVault(patientId) {
  const box = $("vaultList");
  if (!box) return;
  if (!patientId) { box.innerHTML = `<p class="small muted">No patient ID, so nothing is stored.</p>`; return; }
  try {
    const r = await fetch(`/api/v1/patient-records/${encodeURIComponent(patientId)}/documents`);
    const d = await r.json();
    renderPatientVault((d.documents || []).filter((x) => safeUrl(x.view_url)), patientId);
  } catch (_) {
    box.innerHTML = `<p class="small muted">Stored documents could not be loaded just now.</p>`;
  }
}

let vaultDocs = [];
function renderPatientVault(docs, patientId) {
  vaultDocs = docs;
  const box = $("vaultList");
  box.dataset.patient = patientId;
  box.innerHTML = docs.length ? docs.map((d, i) => `
    <div class="vault-row"><span class="tile-icon">${icon(/\.pdf$/i.test(d.filename) ? "doc" : "camera")}</span>
      <div class="vault-meta"><b>${esc(d.filename)}</b><span>${esc(d.doc_type || "Document")} · ${d.size_kb || 0} KB</span></div>
      <div class="vault-act"><button type="button" class="btn btn-secondary btn-sm" data-act="view" data-i="${i}">View</button>
        <button type="button" class="btn btn-secondary btn-sm" data-act="delete" data-i="${i}">Delete</button></div></div>`).join("")
    : `<div class="empty"><b>No documents stored</b>Prescriptions uploaded for this patient are kept here.</div>`;
}

async function deleteVaultDoc(i) {
  const d = vaultDocs[i], patient = $("vaultList").dataset.patient;
  if (!d || !confirm(`Delete ${d.filename} from this patient's stored documents?`)) return;
  const r = await fetch(`/api/v1/patient-records/${encodeURIComponent(patient)}/documents/${encodeURIComponent(d.filename)}`, { method: "DELETE" });
  notify(r.ok ? "Document deleted" : "Could not delete it", "", r.ok ? "good" : "info");
  loadPatientVault(patient);
}

// ---------------------------------------------------------------- past encounters and nutrition from lab results
function renderEncounters(list) {
  const items = list || [];
  setSection("encountersSection", items.length > 0);
  $("encountersCount").textContent = `${items.length} ${items.length === 1 ? "visit" : "visits"}`;
  $("encountersList").innerHTML = items.map((e) => `
    <div class="enc">
      <div class="row-between"><div><b>${esc(e.facility_or_doctor || "Sanjeevani facility")}</b><span class="muted small" style="display:block">${esc(e.encounter_type || "Consultation")}</span></div>
        <span class="chip">${esc(e.date || "Earlier visit")}</span></div>
      <ul class="bullets">${(e.key_points || []).slice(0, 3).map((p) => `<li>${esc(p)}</li>`).join("") || `<li>${esc(e.summary || "Visit recorded.")}</li>`}</ul>
    </div>`).join("");
}

// A list from the model, or a single block of text from an older plan: either way, short bullet points.
function bulletsHtml(items, fallback) {
  const arr = Array.isArray(items) ? items : typeof items === "string" ? items.split(/\n|(?<=\.)\s+(?=[A-Z])/) : [];
  const clean = arr.map((p) => String(p || "").replace(/^[-*•\d.\s]+/, "").trim()).filter(Boolean);
  return (clean.length ? clean : [fallback]).map((p) => `<li>${esc(p)}</li>`).join("");
}

function renderDiet(diet) {
  $("emailDietGuidelines").innerHTML = bulletsHtml(diet && diet.dietary_guidelines, "Eat balanced meals and drink plenty of water.");
  $("emailLifestyleGuidelines").innerHTML = bulletsHtml(diet && diet.lifestyle_guidelines, "Stay active and keep a regular routine.");
  const bio = (diet && diet.has_lab_biomarkers !== false && diet.biomarker_nutrition_insights) || [];
  $("biomarkerBox").hidden = !bio.length;
  $("biomarkerList").innerHTML = bio.map((b) => `
    <div class="bio"><div class="row-between"><b>${esc(b.biomarker || "Result")}</b>
      <span class="small muted">${b.previous_value ? `${esc(b.previous_value)} → ` : ""}${esc(b.current_value || "")}</span></div>
      ${b.clinical_trend ? `<p class="small muted">${esc(b.clinical_trend)}</p>` : ""}
      <p>${esc(b.actionable_dietary_guidance || "")}</p></div>`).join("");
}

// ---------------------------------------------------------------- one click handler per list, so no file name is ever put in markup that runs
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const i = Number(b.dataset.i), host = b.parentElement.closest("#prescriptionFilesDisplay, #onFileDocs, #vaultList");
  if (!host) return;
  if (host.id === "prescriptionFilesDisplay") {
    if (b.dataset.act === "remove") { selectedPrescriptionFiles.splice(i, 1); renderPrescriptionFiles(); }
    else if (selectedPrescriptionFiles[i]) openDocViewer(URL.createObjectURL(selectedPrescriptionFiles[i]), selectedPrescriptionFiles[i].name, "Prescription you are adding");
  } else if (host.id === "onFileDocs" && b.dataset.act === "view" && onFileDocs[i]) {
    openDocViewer(safeUrl(onFileDocs[i].view_url), onFileDocs[i].filename, "On file for this patient");
  } else if (host.id === "vaultList") {
    if (b.dataset.act === "view" && vaultDocs[i]) openDocViewer(safeUrl(vaultDocs[i].view_url), vaultDocs[i].filename, `Patient ${host.dataset.patient}`);
    else if (b.dataset.act === "delete") deleteVaultDoc(i);
  }
});
document.addEventListener("change", (e) => {
  const c = e.target.closest('#onFileDocs [data-act="use"]');
  if (!c || !onFileDocs[Number(c.dataset.i)]) return;
  const name = onFileDocs[Number(c.dataset.i)].filename;
  c.checked ? onFileUse.add(name) : onFileUse.delete(name);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("docViewer").classList.contains("hidden")) closeDocViewer(); });

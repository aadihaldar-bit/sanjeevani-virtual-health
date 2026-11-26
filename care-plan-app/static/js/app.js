// Sanjeevani Virtual Health app: care plan, voice questions, ordering, doctors, labs, pharmacy and insights.
"use strict";

// ---------------------------------------------------------------- state
let currentTab = "care-journey";
let latestApiResponse = null;
let medicineOrderState = [];
let selectedFulfillment = "home_delivery";
let inboundCallActive = false;
let inboundInterval = null;
let inboundSeconds = 0;
const chartInstances = {};
let voicebotPatients = [];
window.voicebotPatients = voicebotPatients;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const initials = (name) => (name || "").replace(/^(Dr\.?|Master|Mr\.?|Ms\.?|Mrs\.?)\s+/i, "").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
window.$ = $;
window.esc = esc;
window.initials = initials;

function notify(title, text, kind = "good") {
  const t = document.createElement("div");
  t.className = "toast glass";
  t.innerHTML = `${icon(kind === "good" ? "check-circle" : "info")}<div><b>${esc(title)}</b>${text ? `<p>${esc(text)}</p>` : ""}</div>`;
  $("toasts").appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, 4600);
}

function populatePatientPresets(patients) {
  const sel = $("patientPresetSelect");
  if (!sel) return;
  const countEl = $("patientSelectCount");
  if (countEl) countEl.textContent = patients && patients.length ? `(${patients.length} registered)` : "";
  const opts = ['<option value="">-- Choose registered patient or enter manually --</option>'];
  (patients || []).forEach((p) => {
    const val = p.external_id || p.phone_e164;
    const name = p.name || `${p.first_name || ""} ${p.last_name || ""}`.trim() || "Patient";
    const label = `${name} (${p.phone_e164 || ""}${p.external_id ? ' · ' + p.external_id : ''})`;
    opts.push(`<option value="${esc(val)}">${esc(label)}</option>`);
  });
  sel.innerHTML = opts.join("");
}
window.populatePatientPresets = populatePatientPresets;

function onSelectPatientPreset(val) {
  if (!val) {
    if ($("patient_name")) $("patient_name").value = "";
    if ($("patient_id")) $("patient_id").value = "";
    if ($("mobile_number")) $("mobile_number").value = "";
    if ($("identity_dob")) $("identity_dob").value = "";
    if ($("patient_age")) $("patient_age").value = "";
    if ($("delivery_address")) $("delivery_address").value = "";
    if ($("chronic_conditions")) $("chronic_conditions").value = "";
    if ($("blood_pressure")) $("blood_pressure").value = "";
    if ($("weight_kg")) $("weight_kg").value = "";
    const callTarget = $("callTargetChip");
    if (callTarget) callTarget.textContent = "";
    const docCallTarget = $("docCallTargetChip");
    if (docCallTarget) docCallTarget.textContent = "";
    const status = $("fetchHistoryStatus");
    if (status) { status.hidden = true; status.textContent = ""; }
    return;
  }
  const list = (typeof voicebotPatients !== "undefined" && voicebotPatients && voicebotPatients.length) ? voicebotPatients : (window.voicebotPatients || []);
  const p = list.find((item) => (item.external_id === val || item.phone_e164 === val));
  if (!p) return;

  const name = p.name || `${p.first_name || ""} ${p.last_name || ""}`.trim() || "";
  if ($("patient_name")) $("patient_name").value = name;
  if ($("patient_id")) $("patient_id").value = p.external_id || "";
  if ($("mobile_number")) $("mobile_number").value = p.phone_e164 || "";
  if ($("identity_dob")) $("identity_dob").value = p.identity_dob || "";

  if (p.identity_dob) {
    const dob = new Date(p.identity_dob);
    if (!isNaN(dob.getTime())) {
      const today = new Date();
      let age = today.getFullYear() - dob.getFullYear();
      const m = today.getMonth() - dob.getMonth();
      if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
      if (age >= 0 && $("patient_age")) $("patient_age").value = age;
    }
  }

  if (p.gender && $("patient_gender")) {
    $("patient_gender").value = p.gender;
  }

  const callTarget = $("callTargetChip");
  if (callTarget) {
    callTarget.textContent = `Target: ${name} (${p.phone_e164 || ""})`;
  }
  const docCallTarget = $("docCallTargetChip");
  if (docCallTarget) {
    docCallTarget.textContent = `Target: ${name} (${p.phone_e164 || ""})`;
  }

  fetchPatientHistory(false);
}
window.onSelectPatientPreset = onSelectPatientPreset;

async function loadVoicebotPatients() {
  try {
    const res = await fetch("/api/v1/patients");
    if (!res.ok) return [];
    const data = await res.json();
    voicebotPatients = data.patients || [];
    window.voicebotPatients = voicebotPatients;
    populatePatientPresets(voicebotPatients);
    if (typeof renderPatientRosterCards === "function") {
      renderPatientRosterCards(voicebotPatients);
    }
    return voicebotPatients;
  } catch (err) {
    console.warn("Could not load patients from DB:", err);
    return [];
  }
}
window.loadVoicebotPatients = loadVoicebotPatients;

// ---------------------------------------------------------------- sample data
const sampleCases = {
  gastro: `SANJEEVANI HOSPITAL - CONSULTATION RECORD
Doctor: Dr. R. Keshwani, General Physician and Diabetologist
Diagnosis: Acute Gastroenteritis with mild dehydration

Prescriptions:
1. TAB OFLAZEST OZ - 1 tablet BD x 3 days (after food)
2. TAB AZENAC MR - 1 tablet BD x 3 days (after food)
3. TAB ANDIAL - 1 tablet TDS x 2 days (as needed for diarrhea)
4. TAB ZOFER 4mg - 1 tablet BD x 2 days (before food for nausea)

Advice: Rehydration with ORS, clear broths, low residue diet. Follow up in 3 days if symptoms persist.
Device: Smart Digital Thermometer for temperature tracking.`,

  pediatric: `SANJEEVANI MEDICAL CITY - PEDIATRICS CLINIC
Doctor: Dr. Sonia, Senior Consultant Pediatrician
Diagnosis: Febrile Illness / Acute Upper Respiratory Infection with Fever

Prescriptions:
1. Crocin DS Syrup - 4 ml TDS for 3 days (for fever and pain relief)
2. Meftal-P Syrup - 4 ml BD for 2 days (for high fever SOS)
3. Ephodrex Syrup - 4 ml BD for 3 days (for respiratory congestion)

Advice: Maintain frequent temperature logging on temperature chart. Light clothing, ample hydration.
Device: Smart Connected Infrared Thermometer (Continuous Sync)
Lab: CBC and CRP if fever persists beyond 48 hours.`,
};

const doctorsData = [
  { id: 1, name: "Dr. Mansoor Ali", dept: "Endocrinology", title: "Consultant diabetologist and endocrinologist", hospital: "Sanjeevani Medical City, Abu Dhabi", rating: "4.9", reviews: 184, available: "Today, 16:30" },
  { id: 2, name: "Dr. R. Keshwani", dept: "Gastroenterology", title: "General physician and gastroenterologist", hospital: "Sanjeevani Hospital Al Najda, Abu Dhabi", rating: "4.9", reviews: 320, available: "Tomorrow, 10:00" },
  { id: 3, name: "Dr. Sonia", dept: "Pediatrics", title: "Senior consultant paediatrician", hospital: "Sanjeevani Medical City, Abu Dhabi", rating: "5.0", reviews: 210, available: "Today, 18:00" },
  { id: 4, name: "Dr. Reynaldo Joson", dept: "Oncology", title: "Consultant surgical oncologist and thyroid specialist", hospital: "Sanjeevani Medical City, Abu Dhabi", rating: "4.8", reviews: 95, available: "26 Oct, 11:30" },
  { id: 5, name: "Dr. Arun Sharma", dept: "Cardiology", title: "Consultant interventional cardiologist", hospital: "Sanjeevani Specialty Hospital, Sharjah", rating: "4.9", reviews: 142, available: "Tomorrow, 14:15" },
  { id: 6, name: "Dr. Fatima Al Nuaimi", dept: "Orthopedics", title: "Consultant orthopaedic surgeon", hospital: "Sanjeevani Royal Hospital, Al Ain", rating: "4.9", reviews: 88, available: "Today, 17:00" },
];
const AVATAR_COLORS = ["#0c2340", "#1d8a5a", "#0a62d0", "#7a4bb5", "#b5541a", "#2f6f8f"];

const labsData = [
  { id: 1, name: "Comprehensive metabolic panel (CMP)", category: "Blood test", fasting: "Fast for 10-12 hours", time: "Same day, about 4 hours", price: "AED 290", home: true },
  { id: 2, name: "HbA1c (average blood sugar)", category: "Diabetes", fasting: "No fasting needed", time: "About 2 hours", price: "AED 120", home: true },
  { id: 3, name: "Thyroid ultrasound and fine-needle biopsy", category: "Radiology", fasting: "No fasting needed", time: "24-48 hours", price: "AED 850", home: false },
  { id: 4, name: "Complete blood count (CBC) with CRP", category: "Infection", fasting: "No fasting needed", time: "About 3 hours", price: "AED 180", home: true },
  { id: 5, name: "Lipid profile (cholesterol, HDL, LDL)", category: "Heart", fasting: "Fast for 12 hours", time: "About 4 hours", price: "AED 195", home: true },
  { id: 6, name: "Stool routine and occult blood", category: "Digestive", fasting: "No fasting needed", time: "About 6 hours", price: "AED 140", home: true },
];

const pharmacyProductsData = [
  { id: 101, name: "Connected blood pressure monitor", category: "RPM Device", price: 149, unit: "Device", badge: "Home device", desc: "Readings sync to your Sanjeevani record over Bluetooth." },
  { id: 102, name: "Infrared forehead thermometer", category: "RPM Device", price: 89, unit: "Device", badge: "Home device", desc: "One-second readings, logged for fever care." },
  { id: 103, name: "Glucometer kit with 50 test strips", category: "RPM Device", price: 129, unit: "Starter kit", badge: "Home device", desc: "Blood sugar readings shared with your doctor." },
  { id: 104, name: "Fingertip pulse oximeter", category: "RPM Device", price: 75, unit: "Device", badge: "Home device", desc: "Blood oxygen (SpO2) and pulse rate." },
  { id: 105, name: "Oflazest OZ, 10 tablets", category: "Antibiotics & Gastro", price: 42, unit: "Strip of 10", badge: "Prescription", desc: "Ofloxacin and ornidazole for stomach infections." },
  { id: 106, name: "Azenac MR, 10 tablets", category: "Pain & Relief", price: 38, unit: "Strip of 10", badge: "Prescription", desc: "Aceclofenac and paracetamol for pain and spasm." },
  { id: 107, name: "Andial 2 mg, 10 capsules", category: "Antibiotics & Gastro", price: 28, unit: "Strip of 10", badge: "Prescription", desc: "Loperamide for diarrhoea." },
  { id: 108, name: "Zofer 4 mg, 10 tablets", category: "Antibiotics & Gastro", price: 32, unit: "Strip of 10", badge: "Prescription", desc: "Ondansetron for nausea and vomiting." },
  { id: 109, name: "Crocin DS suspension, 60 ml", category: "Pediatric Care", price: 18, unit: "Bottle", badge: "Children", desc: "Paracetamol 240 mg/5 ml for fever and pain." },
  { id: 110, name: "Ephodrex syrup, 100 ml", category: "Pediatric Care", price: 24, unit: "Bottle", badge: "Children", desc: "For children's cough and chest congestion." },
  { id: 111, name: "Meftal-P suspension, 60 ml", category: "Pediatric Care", price: 22, unit: "Bottle", badge: "Children", desc: "Mefenamic acid for high fever." },
  { id: 112, name: "Rehydration sachets, pack of 6", category: "Rehydration & OTC", price: 25, unit: "Pack", badge: "Everyday", desc: "WHO-standard oral rehydration salts." },
  { id: 113, name: "Vitamin C 1000 mg with zinc, 20 tablets", category: "Vitamins & Wellness", price: 35, unit: "Tube", badge: "Everyday", desc: "Effervescent daily supplement." },
];

function productLook(p) {
  if (p.category.includes("RPM")) return { cls: "cat-rpm", ico: "watch" };
  if (p.category.includes("Pediatric")) return { cls: "cat-kids", ico: "baby" };
  if (p.category.includes("Rehydration")) return { cls: "cat-otc", ico: "drop" };
  if (p.category.includes("Vitamins")) return { cls: "cat-otc", ico: "leaf" };
  return { cls: "cat-rx", ico: "pill" };
}

// ---------------------------------------------------------------- navigation
const TABS = ["care-journey", "patient", "appointments", "alerts", "vitals", "doctors", "labs", "pharmacy", "analytics"];

function switchTab(tabId) {
  if (!TABS.includes(tabId)) tabId = "care-journey";
  currentTab = tabId;
  TABS.forEach((t) => $(`view-${t}`).classList.toggle("hidden", t !== tabId));
  document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("is-active", b.dataset.tab === tabId));
  if (location.hash !== `#${tabId}`) history.replaceState(null, "", `#${tabId}`);
  window.scrollTo({ top: 0, behavior: "instant" });
  if (tabId === "doctors") {
    renderDoctors();
    if (typeof updateDocCallTargetChip === "function") updateDocCallTargetChip();
  }
  if (tabId === "labs") renderLabs();
  if (tabId === "pharmacy") { renderPharmacyProducts(); renderPharmacyCartUI(); }
  if (tabId === "analytics") { initAnalyticsCharts(); if (typeof loadLiveInsights === "function") loadLiveInsights(); }
  if (tabId === "patient" && typeof openPatientView === "function") openPatientView();
  if (tabId === "appointments" && typeof openAppointmentsView === "function") openAppointmentsView();
  if (tabId === "alerts" && typeof openAlertsView === "function") openAlertsView();
}

function toggleDisclose(id) {
  const d = $(id);
  d.classList.toggle("open");
  d.querySelector("button").setAttribute("aria-expanded", d.classList.contains("open"));
}
function toggleDemographics() { toggleDisclose("demoDisclose"); }

// ---------------------------------------------------------------- medical history (files and record look-up)
let selectedHistoryFiles = [];

function handleHistoryFilesSelected(input) {
  Array.from(input.files || []).forEach((f) => {
    if (!selectedHistoryFiles.some((x) => x.name === f.name && x.size === f.size)) selectedHistoryFiles.push(f);
  });
  input.value = "";                      // so more files can be added without losing these
  renderHistoryFilesList();
}

function removeHistoryFile(i) {
  selectedHistoryFiles.splice(i, 1);
  renderHistoryFilesList();
}

function renderHistoryFilesList() {
  const box = $("historyFilesDisplay");
  box.hidden = !selectedHistoryFiles.length;
  box.innerHTML = selectedHistoryFiles.map((f, i) => `
    <span class="file-chip">${icon("doc")}<span>${esc(f.name)}</span><small>${Math.max(1, Math.round(f.size / 1024))} KB</small>
      <button type="button" onclick="removeHistoryFile(${i})" aria-label="Remove ${esc(f.name)}">${icon("x")}</button></span>`).join("");
}

let fetchPatientHistoryTimer = null;
function debounceFetchPatientHistory() {
  $("fetchHistoryStatus").hidden = true;
  clearTimeout(fetchPatientHistoryTimer);
  // manual lookup only on button click
}

function setHistoryStatus(text, kind) {
  const el = $("fetchHistoryStatus");
  if (!el) return;
  el.textContent = text;
  if (el.style) el.style.color = kind === "good" ? "var(--good)" : "var(--warn)";
  el.hidden = !text;
}

// Fill the form from the patient's earlier visits (stored in MongoDB by previous care plans).
async function fetchPatientHistory(isManual = false) {
  const patId = $("patient_id").value.trim();
  if (!patId) {
    setHistoryStatus("", "");
    if (isManual) notify("Enter a patient ID first", "For example SNJ-98214.", "info");
    return;
  }
  const btn = $("btnFetchHistory");
  btn.disabled = true;
  btn.textContent = "Looking...";
  try {
    const result = await (await fetch(`/api/v1/patients/${encodeURIComponent(patId)}/history`)).json();
    if (result.status === "success" && result.data && result.data.found) {
      const d = result.data;
      const set = (id, v) => { if (v) $(id).value = v; };
      set("patient_name", d.patient_name);
      set("mobile_number", d.mobile_number);
      set("identity_dob", d.identity_dob);
      set("patient_age", d.patient_age);
      set("patient_gender", d.patient_gender);
      set("delivery_address", d.delivery_address);
      const conds = d.chronic_conditions || [];
      if (conds.length) $("chronic_conditions").value = conds.join(", ");
      const lines = [];
      if (conds.length) lines.push(`Diagnosed conditions: ${conds.join(", ")}`);
      if (d.past_medications && d.past_medications.length) {
        lines.push(`Previous prescriptions on file: ${d.past_medications.map((m) => `${m.drug_name} (${m.strength || "standard"})`).join(", ")}`);
      }
      if (lines.length) {
        $("history_text").value = lines.join("\n");
        if (!$("historyDisclose").classList.contains("open")) toggleDisclose("historyDisclose");
      }
      setHistoryStatus(`Found ${d.prescriptions_count || 0} earlier ${d.prescriptions_count === 1 ? "visit" : "visits"}; details filled in.`, "good");
      renderOnFileDocs(d.stored_documents || [], patId);
    } else {
      // Fallback to voicebot patient-record if not found in local MongoDB
      let foundInVoicebot = false;
      if (VOICE_CONNECTED) {
        try {
          const isPhone = /^\+?\d[\d\s-]{6,}$/.test(patId);
          const params = new URLSearchParams();
          if (isPhone) {
            params.set("phone", patId.replace(/[\s-]/g, ""));
          } else {
            params.set("patient_id", patId);
            const curPhone = ($("mobile_number")?.value || "").trim();
            if (curPhone && /^\+?\d[\d\s-]{6,}$/.test(curPhone)) {
              params.set("phone", curPhone.replace(/[\s-]/g, ""));
            }
          }
          const vbRes = await fetch(`/api/v1/voice/patient-record?${params}`);
          if (vbRes.ok) {
            const vbData = await vbRes.json();
            const p = vbData.patient || {};
            if (p.found || p.name) {
              foundInVoicebot = true;
              const set = (id, v) => { if (v && $(id)) $(id).value = v; };
              set("patient_name", p.name);
              set("patient_id", p.external_id || patId);
              set("mobile_number", p.phone || p.phone_e164);
              set("identity_dob", p.identity_dob);
              if (p.identity_dob) {
                const dob = new Date(p.identity_dob);
                if (!isNaN(dob.getTime())) {
                  const today = new Date();
                  let age = today.getFullYear() - dob.getFullYear();
                  const m = today.getMonth() - dob.getMonth();
                  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
                  if (age >= 0 && $("patient_age")) $("patient_age").value = age;
                }
              }
              const lines = [];
              const rxList = vbData.prescriptions || [];
              if (rxList.length) {
                const meds = [];
                rxList.forEach((rx) => {
                  (rx.items || []).forEach((item) => {
                    if (item.drug_name) meds.push(`${item.drug_name}${item.strength ? ` (${item.strength})` : ""}`);
                  });
                });
                if (meds.length) {
                  lines.push(`Previous voicebot prescriptions on file: ${meds.join(", ")}`);
                }
              }
              const calls = vbData.calls || [];
              if (calls.length) {
                lines.push(`Voice assistant activity: ${calls.length} prior call(s) logged.`);
              }
              if (lines.length && $("history_text")) {
                $("history_text").value = lines.join("\n");
                if (!$("historyDisclose").classList.contains("open")) toggleDisclose("historyDisclose");
              }
              setHistoryStatus(`Found Voicebot record for ${p.name} (${rxList.length} prescriptions, ${calls.length} calls); details filled in.`, "good");
            }
          }
        } catch (vbErr) {
          console.warn("Voicebot fallback lookup failed:", vbErr);
        }
      }
      if (!foundInVoicebot) {
        setHistoryStatus("No earlier records for this ID. They will be saved with this visit.", "warn");
      }
    }
  } catch (err) {
    console.error("History look-up failed:", err);
    setHistoryStatus("Records could not be checked right now.", "warn");
  } finally {
    btn.disabled = false;
    btn.textContent = "Look up";
  }
}

function setRxMode(mode) {
  document.querySelectorAll("[data-rx-mode]").forEach((b) => b.classList.toggle("is-active", b.dataset.rxMode === mode));
  $("rxModeText").hidden = mode !== "text";
  $("rxModeFile").hidden = mode !== "file";
}

function loadSampleCase(type) {
  if (!sampleCases[type]) return;
  setRxMode("text");
  $("prescription_text").value = sampleCases[type];
  $("prescription_text").focus();
}

// ---------------------------------------------------------------- care plan
async function handleFormSubmit(e) {
  e.preventDefault();
  const formData = new FormData($("journeyForm"));
  formData.delete("history_files");
  selectedHistoryFiles.forEach((f) => formData.append("history_files", f));
  formData.delete("prescription_files");
  selectedPrescriptionFiles.forEach((f) => formData.append("prescription_files", f));
  const included = includedHistoryDocuments();                  // which stored documents to read as history
  if (included !== null) formData.append("included_history_documents", included);
  const hasRx = (formData.get("prescription_text") || "").trim() || selectedPrescriptionFiles.length;
  const hasHistory = (formData.get("history_text") || "").trim() || selectedHistoryFiles.length;
  if (!hasRx && !hasHistory) {
    notify("Add a prescription first", "Type or paste it, or upload a photo of it.", "info");
    return;
  }

  // Automatically upsert patient to Voicebot directory if voicebot is connected
  const fullName = ($("patient_name")?.value || "").trim();
  const nameParts = fullName.split(/\s+/);
  const firstName = nameParts[0] || "Patient";
  const lastName = nameParts.length > 1 ? nameParts.slice(1).join(" ") : null;
  const mobile = ($("mobile_number")?.value || "").trim();
  const externalId = ($("patient_id")?.value || "").trim();
  const dob = ($("identity_dob")?.value || "").trim();
  const consent = $("consent_calls") ? $("consent_calls").checked : false;

  if (VOICE_CONNECTED && mobile) {
    fetch("/api/v1/voice/patients/upsert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        phone_e164: mobile,
        first_name: firstName,
        last_name: lastName,
        external_id: externalId || null,
        identity_dob: dob || null,
        consent_calls: consent,
      }),
    })
      .then((r) => r.json())
      .then(() => loadVoicebotPatients())
      .catch((err) => console.warn("Patient upsert to voicebot error:", err));
  }

  $("loadingIndicator").classList.remove("hidden");
  $("resultsContainer").classList.add("hidden");
  $("btnSubmitJourney").disabled = true;
  const stopStages = runStages();
  $("loadingIndicator").scrollIntoView({ behavior: "smooth", block: "center" });
  try {
    const response = await fetch("/api/v1/journey/prescription-trigger", { method: "POST", body: formData });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || "the server returned an error");
    }
    latestApiResponse = await response.json();
    renderCarePlanResults(latestApiResponse);
    $("resultsContainer").classList.remove("hidden");
    $("resultsContainer").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    console.error("Pipeline error:", err);
    const slow = err instanceof TypeError || /server returned|timed? ?out|524|504/i.test(err.message || "");
    notify("The prescription could not be read", slow ? "It took too long to answer. Please try again, or use a clearer photo." : err.message, "info");
  } finally {
    stopStages();
    $("loadingIndicator").classList.add("hidden");
    $("btnSubmitJourney").disabled = false;
  }
}

// The three steps move on a timer, so the wait reads as progress. They are an estimate, not a report from the server.
function runStages() {
  const items = [...document.querySelectorAll("#stageList li")];
  const at = (n) => items.forEach((li, i) => { li.className = i < n ? "done" : i === n ? "now" : ""; });
  at(0);
  const timers = [setTimeout(() => at(1), 3500), setTimeout(() => at(2), 9000)];
  return () => timers.forEach(clearTimeout);
}

function setSection(id, show) { $(id).classList.toggle("hidden", !show); }

// The history the reasoner read from the notes, the files and the patient's earlier visits.
function renderPatientHistory(h, patId) {
  renderEncounters(h.chronological_encounters || []);
  const isNone = (list, word) => !list.length || (list.length === 1 && list[0].toLowerCase().includes(word));
  const chronic = h.chronic_conditions || [], allergies = h.allergies || [], pastMeds = h.past_medications || [];
  const hasAny = !isNone(chronic, "none") || !isNone(allergies, "no known") || pastMeds.length || h.clinical_profile_overview;
  setSection("clinicalProfileHistorySection", hasAny);
  if (!hasAny) return;
  $("profileHistoryPatientId").innerText = patId;
  $("profileChronicConditionsList").innerHTML = isNone(chronic, "none")
    ? `<span class="muted small">None reported</span>` : chronic.map((c) => `<span class="chip">${esc(c)}</span>`).join("");
  $("profileAllergiesList").innerHTML = isNone(allergies, "no known")
    ? `<span class="muted small">No known drug allergies</span>` : allergies.map((a) => `<span class="chip chip-bad">${icon("alert")}${esc(a)}</span>`).join("");
  $("profilePastMedsList").innerHTML = pastMeds.length
    ? pastMeds.map((m) => `<div>${esc(m.drug_name || "Medicine")} <span class="muted">${esc(m.dosage || m.strength || "")}</span></div>`).join("")
    : `<span class="muted">None recorded</span>`;
  $("profileClinicalOverviewText").innerText = h.clinical_profile_overview || "";
}

function renderCarePlanResults(data) {
  const summary = data.ai_clinical_summary || {};
  const explanation = data.ai_patient_explanation || {};
  const nba = data.ai_next_best_actions || {};
  const pContext = data.patient_context || {};

  $("emailDoctorHeader").innerText = `${summary.doctor_name || "Attending physician"}, Sanjeevani Healthcare`;
  $("emailUrgencyBadge").innerText = summary.urgency_level || "Routine";
  $("emailDateDisplay").innerText = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const patId = pContext.patient_id || $("patient_id")?.value || "SNJ-98214";
  const patName = pContext.patient_name || $("patient_name")?.value || "Patient";
  const patPhone = pContext.mobile_number || $("mobile_number")?.value || "+919380361678";
  $("emailPatientRefDisplay").innerText = patId;
  $("topPatientName").innerText = `${patName} (${patId})`;
  $("topPatientInitials").innerText = initials(patName);

  const callChip = $("callTargetChip");
  if (callChip) {
    callChip.textContent = `Target: ${patName} (${patPhone})`;
  }
  const docChip = $("docCallTargetChip");
  if (docChip) {
    docChip.textContent = `Target: ${patName} (${patPhone})`;
  }

  $("emailGreeting").innerText = explanation.greeting || `Dear ${patName},`;
  $("emailEnglishSummary").innerText = explanation.friendly_overview || "Your care plan is ready.";
  $("emailArabicSummary").innerText = explanation.arabic_summary || "تم إعداد خطتك العلاجية.";

  renderPatientHistory(data.patient_history || {}, patId);
  stopRealCall();
  $("realCallPanel").hidden = true;
  resetCallButton("Call Patient to Explain Medicines");
  showFollowUp((data.voice_bot_integration || {}).follow_up_calls);
  if (typeof renderFollowUp === "function") renderFollowUp(data);
  if (typeof renderChat === "function") renderChat(data);

  const meds = summary.medications || [];
  $("medsCountBadge").innerText = `${meds.length} ${meds.length === 1 ? "medicine" : "medicines"}`;
  $("emailMedicationsContainer").innerHTML = meds.map((med) => `
    <div class="medrow">
      <div class="medrow-top">
        <b>${esc(med.drug_name)}</b>
        ${med.strength ? `<span class="chip">${esc(med.strength)}</span>` : ""}
        ${med.form ? `<span class="chip">${esc(med.form)}</span>` : ""}
        ${med.frequency_per_day ? `<span class="chip chip-accent">${esc(med.frequency_per_day)} a day</span>` : ""}
        ${med.start_date && med.end_date && !med.duration_assumed ? `<span class="chip dates">${icon("calendar")}${esc(med.start_date)} to ${esc(med.end_date)}</span>` : ""}
      </div>
      <p><b style="font-weight:500">${esc(med.dose_text || "1 dose")}</b>, ${esc(med.timing || "as directed")}</p>
      <p class="muted">${esc(med.instructions_plain || "Take as your doctor advised.")}</p>
      ${med.needs_confirmation ? `<p class="chip chip-warn">${icon("alert")}Please confirm with your doctor: ${esc(med.confirm_note || "this name or dose was hard to read")}</p>` : ""}
      ${med.duration_assumed ? `<p class="muted small">No duration was written, so the dates above are a placeholder. Ask your doctor how long to take it.</p>` : ""}
      ${med.instructions_arabic ? `<p class="arabic" dir="rtl">${esc(med.instructions_arabic)}</p>` : ""}
    </div>`).join("");

  const revisit = nba.doctor_revisit_schedule;
  setSection("emailRevisitSection", revisit && revisit.revisit_recommended !== false);
  if (revisit) {
    $("emailRevisitTiming").innerText = revisit.timing || "In 2 weeks";
    $("emailRevisitPurpose").innerText = revisit.purpose || "Check how the treatment is working.";
  }

  const diet = nba.diet_and_lifestyle;
  setSection("emailDietSection", diet && (diet.dietary_guidelines || diet.lifestyle_guidelines));
  renderDiet(diet);

  const rpm = nba.device_rental_and_rpm;
  let deviceObj = null;
  setSection("emailRpmSection", rpm && rpm.is_recommended !== false);
  if (rpm && rpm.is_recommended !== false) {
    $("emailRpmDeviceName").innerText = rpm.device_name || "Connected health monitor";
    $("emailRpmPurpose").innerText = rpm.purpose || "Readings are shared with your Sanjeevani doctor automatically.";
    deviceObj = { id: 999, drug_name: rpm.device_name || "Connected health monitor", quantity: 1, unit: "Device",
                  duration: "Syncs with your record", price_aed: rpm.purchase_price_aed || 120, isDevice: true, isSelected: true };
  }

  const lab = nba.lab_and_diagnostics;
  setSection("emailLabSection", lab && lab.is_required !== false);
  if (lab) {
    $("emailLabTestName").innerText = lab.test_name || "Blood panel";
    $("emailLabTiming").innerText = lab.timing || "Soon";
    $("emailLabReason").innerText = lab.reason || "To check how you are recovering.";
  }

  const careProg = nba.care_program_enrollment;
  setSection("emailCareProgSection", careProg && careProg.is_recommended !== false);
  if (careProg) {
    $("emailCareProgName").innerText = careProg.program_name || "Sanjeevani care pathway";
    $("emailCareProgList").innerHTML = (careProg.services_included || ["Care navigator", "Dietitian meal plan"])
      .map((s) => `<li>${esc(s)}</li>`).join("");
  }

  initRefillState(meds, deviceObj, pContext.delivery_address);
}

function showResults(data) {
  renderCarePlanResults(data);
}
window.showResults = showResults;

// ---------------------------------------------------------------- the STL voicebot (real calls)
// When the server is connected to the voicebot, "Call Patient to Explain Medicines" phones the patient for real and the follow-up calls can be
// booked from the form. Otherwise the panel keeps the in-browser demo below.
let VOICE_CONNECTED = false;
let realCall = null;          // {id, started, poll, timer}

async function initVoiceConnection() {
  try {
    VOICE_CONNECTED = !!(await (await fetch("/api/v1/voice/status")).json()).configured;
  } catch (_) {
    VOICE_CONNECTED = false;
  }
  $("consentRow").hidden = !VOICE_CONNECTED;
  $("recordNotConnected").hidden = VOICE_CONNECTED;
  if (VOICE_CONNECTED && currentTab === "patient" && typeof openPatientView === "function") openPatientView();   // opened before this answered
  if (VOICE_CONNECTED) {
    $("voiceIntro").textContent = "The Sanjeevani Voice Assistant phones the patient to walk through medication timing, food precautions, and answer any questions.";
    resetCallButton("Call Patient to Explain Medicines");
    await loadVoicebotPatients();
  }
}

function resetCallButton(label) {
  const btn = $("btnToggleVoiceBot");
  if (!btn) return;
  btn.className = "btn btn-light";
  btn.disabled = false;
  btn.innerHTML = `${icon("phone")}<span>${label || "Call Patient to Explain Medicines"}</span>`;
}

const FOLLOW_UP_TEXT = {
  scheduled: (f) => `Follow-up calls booked. The care assistant will call ${f.phone_number} in a few minutes to go through each medicine.`,
  exists: (f) => `Follow-up calls were already booked for this prescription on ${f.phone_number}.`,
  not_allowed: (f) => `Follow-up calls not booked: ${f.detail}.`,
  failed: (f) => `Follow-up calls could not be booked: ${f.detail || "the voice assistant did not answer"}.`,
};

function showFollowUp(f) {
  const el = $("followUpNote");
  const text = f && FOLLOW_UP_TEXT[f.status] ? FOLLOW_UP_TEXT[f.status](f) : "";
  el.hidden = !text;
  el.innerHTML = text ? `${icon(f.status === "scheduled" || f.status === "exists" ? "check-circle" : "info")}<span>${esc(text)}</span>` : "";
}

async function startRealCall() {
  const prescriptionId = latestApiResponse && latestApiResponse.prescription_id;
  if (!prescriptionId) { notify("Create a care plan first", "", "info"); return; }
  const phoneNumber = ($("mobile_number")?.value || "").trim();
  const btn = $("btnToggleVoiceBot");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="mini-spin" style="border-color:rgba(255,255,255,.3);border-top-color:#fff"></span><span>Calling...</span>`;
  }
  try {
    const payload = { prescription_id: prescriptionId };
    if (phoneNumber) payload.phone_number = phoneNumber;
    const r = await fetch("/api/v1/voice/call-me", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "the call could not be placed");
    stopRealCall();
    realCall = { id: d.call_id, started: Date.now() };
    const panel = $("realCallPanel");
    if (panel) panel.hidden = false;
    if ($("realCallSummaryBox")) $("realCallSummaryBox").hidden = true;
    if ($("realCallSummary")) {
      $("realCallSummary").hidden = true;
      $("realCallSummary").textContent = "";
    }
    if ($("realCallWave")) $("realCallWave").hidden = true;
    if ($("realCallTranscript")) $("realCallTranscript").innerHTML = "";
    if ($("realCallText")) $("realCallText").textContent = `Calling ${d.phone_number || phoneNumber || "patient"}...`;
    setCallStage("calling");
    realCall.timer = setInterval(() => {
      const s = Math.floor((Date.now() - realCall.started) / 1000);
      if ($("realCallTimer")) {
        $("realCallTimer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
      }
    }, 1000);
    pollRealCall();
    resetCallButton("Calling...");
    if ($("btnToggleVoiceBot")) $("btnToggleVoiceBot").disabled = true;
  } catch (e) {
    resetCallButton("Call Patient to Explain Medicines");
    notify("The call could not be placed", e.message.charAt(0).toUpperCase() + e.message.slice(1) + ".", "info");
  }
}

function setOrb(id, state) { const o = $(id); if (o) o.dataset.state = state; }
function setCaption(id, who, text) {
  const c = $(id); if (!c) return;
  c.className = "caption" + (who === "you" ? " is-you" : "");
  c.innerHTML = `<span class="who">${who === "agent" ? "Care assistant" : "Patient"}</span>${esc(text)}`;
}

function setCallStage(stage) {
  setOrb("realCallOrb", stage === "ended" ? "idle" : stage === "in_call" ? "listening" : "thinking");
  if (stage === "calling" || stage === "ringing") setCaption("realCallCaption", "agent", stage === "ringing" ? "Ringing the patient" : "Placing the call");
  const order = ["calling", "ringing", "in_call", "ended"];
  const at = order.indexOf(stage);
  document.querySelectorAll("#realCallSteps li").forEach((li) => {
    const i = order.indexOf(li.dataset.stage);
    li.className = i < at || (stage === "ended" && i <= at) ? "done" : i === at ? "now" : "";
  });
  const wave = $("realCallWave");
  if (wave) {
    wave.hidden = (stage !== "in_call");
  }
  const btn = $("btnEndOrDismissCall");
  if (btn) {
    if (stage === "ended") {
      btn.textContent = "Dismiss";
      btn.className = "btn btn-sm btn-light";
    } else {
      btn.textContent = "End call";
      btn.className = "btn btn-sm btn-danger";
    }
  }
}

async function pollRealCall() {
  if (!realCall) return;
  let d;
  try {
    const res = await fetch(`/api/v1/voice/calls/${encodeURIComponent(realCall.id)}`);
    d = await res.json();
    if (!res.ok) throw new Error(d.detail || "status check failed");
  } catch (_) {
    if (realCall) realCall.poll = setTimeout(pollRealCall, 4000);
    return;
  }
  if (!realCall) return;

  if (d.stage && d.stage !== "unknown") setCallStage(d.stage);
  const stageLabels = {
    calling: "Placing the call",
    ringing: "Ringing patient",
    in_call: "On the call",
    ended: "Call ended",
  };
  if ($("realCallText")) {
    $("realCallText").textContent = stageLabels[d.stage] || $("realCallText").textContent;
  }

  if (d.transcript && d.transcript.length) {
    const transcriptEl = $("realCallTranscript");
    if (transcriptEl) {
      const html = d.transcript.map((t) =>
        `<div class="msg ${t.from === "agent" ? "agent" : "patient"}"><b>${t.from === "agent" ? "Sanjeevani Assistant" : "Patient"}:</b> <span>${esc(t.text)}</span></div>`
      ).join("");
      if (transcriptEl.innerHTML !== html) {
        transcriptEl.innerHTML = html;
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
      }
    }
    const last = d.transcript[d.transcript.length - 1];
    if (!d.done && d.stage !== "ended") {
      setCaption("realCallCaption", last.from === "agent" ? "agent" : "you", last.text);
      setOrb("realCallOrb", last.from === "agent" ? "speaking" : "listening");
    }
  }

  if (d.done || d.stage === "ended") {
    if (realCall.timer) clearInterval(realCall.timer);
    if (realCall.poll) clearTimeout(realCall.poll);
    setCallStage("ended");
    if ($("realCallText")) $("realCallText").textContent = "Call ended";
    if (d.summary) {
      if ($("realCallSummaryBox")) $("realCallSummaryBox").hidden = false;
      if ($("realCallSummary")) {
        $("realCallSummary").hidden = false;
        $("realCallSummary").textContent = d.summary;
      }
    }
    notify("Call completed", "Summary recorded.", "good");

    // Refresh patient history if Patient tab is opened
    if (currentTab === "patient" && typeof loadPatientRecord === "function") {
      const patId = (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.patient_id) || $("patient_id")?.value;
      const phone = (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.mobile_number) || $("mobile_number")?.value;
      if ($("recordQuery") && (patId || phone)) $("recordQuery").value = patId || phone;
      loadPatientRecord().catch(() => {});
    }

    realCall = null;
    resetCallButton("Call Patient to Explain Medicines");
    return;
  }

  realCall.poll = setTimeout(pollRealCall, 2500);
}

function stopRealCall() {
  if (!realCall) return;
  if (realCall.poll) clearTimeout(realCall.poll);
  if (realCall.timer) clearInterval(realCall.timer);
  realCall = null;
  const wave = $("realCallWave");
  if (wave) wave.hidden = true;
}

function endOrDismissRealCall() {
  if (realCall) {
    stopRealCall();
    setCallStage("ended");
    if ($("realCallText")) $("realCallText").textContent = "Call ended";
    resetCallButton("Call Patient to Explain Medicines");
    if ($("btnEndOrDismissCall")) {
      $("btnEndOrDismissCall").textContent = "Dismiss";
      $("btnEndOrDismissCall").className = "btn btn-sm btn-light";
    }
  } else {
    const panel = $("realCallPanel");
    if (panel) panel.hidden = true;
    resetCallButton("Call Patient to Explain Medicines");
  }
}
window.endOrDismissRealCall = endOrDismissRealCall;
window.startRealCall = startRealCall;
window.pollRealCall = pollRealCall;
window.stopRealCall = stopRealCall;
window.resetCallButton = resetCallButton;
window.setCallStage = setCallStage;

// ---------------------------------------------------------------- voice questions (demo, used when no voicebot is connected)
function openVoiceFromBar() {
  if (currentTab !== "care-journey") switchTab("care-journey");
  if ($("resultsContainer").classList.contains("hidden")) {
    notify("Create a care plan first", "The assistant answers questions about the medicines in the plan.", "info");
    return;
  }
  // The top bar button opens the text assistant. A phone call is only placed from the voice panel, on purpose.
  if (typeof focusChat === "function" && focusChat()) return;
  $("voicePanel").scrollIntoView({ behavior: "smooth", block: "center" });
  if (VOICE_CONNECTED) return;
  if ($("inboundVoiceBotPanel").classList.contains("hidden")) toggleInboundVoiceBot();
}

function toggleInboundVoiceBot() {
  if (VOICE_CONNECTED) { if (!realCall) startRealCall(); return; }
  if ($("inboundVoiceBotPanel").classList.contains("hidden")) {
    $("inboundVoiceBotPanel").classList.remove("hidden");
    const btn = $("btnToggleVoiceBot");
    btn.className = "btn btn-danger";
    btn.innerHTML = `${icon("phone-off")}<span>End call</span>`;
    startInboundVoiceSession();
  } else {
    endInboundVoiceBot();
  }
}

function startInboundVoiceSession() {
  inboundCallActive = true;
  clearInterval(inboundInterval);
  inboundSeconds = 0;
  updateInboundTimer();
  inboundInterval = setInterval(() => { inboundSeconds++; updateInboundTimer(); }, 1000);
  $("inboundTranscriptContainer").innerHTML = "";
  const patName = $("patient_name")?.value || "there";
  const greeting = `Hello ${patName}, I'm the Sanjeevani care assistant. I have your prescription here. What would you like to know about your medicines?`;
  addInboundTranscript("agent", greeting);
  speakText(greeting);
}

function updateInboundTimer() {
  const m = String(Math.floor(inboundSeconds / 60)).padStart(2, "0");
  const s = String(inboundSeconds % 60).padStart(2, "0");
  $("inboundTimerDisplay").innerText = `${m}:${s}`;
}

function addInboundTranscript(speaker, text) {
  const box = $("inboundTranscriptContainer");
  const agent = speaker === "agent";
  const div = document.createElement("div");
  div.className = `bubble ${agent ? "bot" : "me"}`;
  div.innerHTML = `<small>${agent ? "Care assistant" : "You"}</small>${esc(text)}`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  setCaption("inboundCaption", agent ? "agent" : "you", text);
  setOrb("inboundOrb", agent ? "speaking" : "listening");
  clearTimeout(addInboundTranscript.t);
  addInboundTranscript.t = setTimeout(() => setOrb("inboundOrb", "idle"), agent ? Math.min(14000, 1200 + text.length * 55) : 900);
}

function speakText(text) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.0;
  u.pitch = 1.0;
  window.speechSynthesis.speak(u);
}

function askBotQuestion(questionText) {
  addInboundTranscript("patient", questionText);
  setTimeout(() => {
    let answer = "Your doctor recommends finishing the full course, drinking plenty of water and eating balanced meals.";
    const q = questionText.toLowerCase();
    if (q.includes("milk") || q.includes("juice")) {
      answer = "Take your antibiotic, Oflazest OZ, with plain water. The calcium in milk and dairy can stop the medicine being absorbed properly.";
    } else if (q.includes("miss") || q.includes("dose")) {
      answer = "Take the missed dose as soon as you remember. If your next dose is due within a few hours, skip it and carry on as normal. Never take two doses at once.";
    } else if (q.includes("food") || q.includes("avoid")) {
      answer = "Avoid spicy, greasy or heavy dairy foods while you recover. Clear soups, rice, bananas and rehydration drinks are easy on the stomach.";
    } else if (q.includes("days") || q.includes("continue")) {
      answer = "Finish the full three-day course Dr. Keshwani prescribed, even if you feel better after the first day, so the infection clears completely.";
    }
    addInboundTranscript("agent", answer);
    speakText(answer);
  }, 800);
}

function escalateToLiveDoctor() {
  const line = "Connecting you to the Sanjeevani duty doctor now. Please stay on the line.";
  addInboundTranscript("agent", line);
  speakText(line);
  setTimeout(() => notify("Connecting to the duty doctor", "Dr. Mansoor Ali, Sanjeevani Medical City, is joining the call."), 1200);
}

function endInboundVoiceBot() {
  inboundCallActive = false;
  clearInterval(inboundInterval);
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  $("inboundVoiceBotPanel").classList.add("hidden");
  const btn = $("btnToggleVoiceBot");
  btn.className = "btn btn-light";
  btn.innerHTML = `${icon("phone")}<span>Start a call</span>`;
}

// ---------------------------------------------------------------- order from the care plan
function initRefillState(meds, deviceObj, initialAddress) {
  medicineOrderState = meds.map((m, idx) => {
    const d = `${m.drug_name} ${m.duration || ""}`.toLowerCase();
    const unit = d.includes("syrup") || d.includes("suspension") ? "Bottle"
      : d.includes("tab") || d.includes("cap") ? "Strip of 10" : "Pack";
    return { id: idx + 1, drug_name: m.drug_name, quantity: 1, unit, duration: m.duration || "As prescribed", isDevice: false, isSelected: true };
  });
  if (deviceObj) medicineOrderState.push(deviceObj);
  if (initialAddress) $("finalDeliveryAddress").value = initialAddress;
  $("pharmacyAlertBox").classList.add("hidden");
  renderChecklistUI();
}

function stepper(onMinus, onPlus, value, label) {
  return `<div class="stepper" role="group" aria-label="${esc(label)}">
    <button type="button" onclick="${onMinus}" aria-label="Fewer">${icon("minus")}</button>
    <output>${value}</output>
    <button type="button" onclick="${onPlus}" aria-label="More">${icon("plus")}</button></div>`;
}

function renderChecklistUI() {
  const selected = medicineOrderState.filter((i) => i.isSelected).length;
  $("pharmacyChecklistContainer").innerHTML = medicineOrderState.map((item) => `
    <div class="check-item ${item.isSelected ? "" : "off"}">
      <input type="checkbox" id="med_check_${item.id}" ${item.isSelected ? "checked" : ""} onchange="toggleMedicineSelect(${item.id})">
      <span class="prod-ico ${item.isDevice ? "cat-rpm" : "cat-rx"}" style="width:38px;height:38px;border-radius:11px">${icon(item.isDevice ? "watch" : "pill")}</span>
      <label for="med_check_${item.id}"><b>${esc(item.drug_name)}</b><span>${esc(item.duration)}${item.isDevice ? " · home device" : ""}</span></label>
      <span class="small muted" style="white-space:nowrap">${esc(item.unit)}</span>
      ${item.isSelected ? stepper(`adjustQty(${item.id}, -1)`, `adjustQty(${item.id}, 1)`, item.quantity, `Quantity of ${item.drug_name}`) : ""}
    </div>`).join("");
  $("selectedMedsCountDisplay").innerText = `${selected} of ${medicineOrderState.length} selected`;
  $("btnPharmacy1Click").classList.toggle("is-disabled", selected === 0);
}

function toggleMedicineSelect(id) {
  const item = medicineOrderState.find((m) => m.id === id);
  if (item) { item.isSelected = !item.isSelected; renderChecklistUI(); }
}

function adjustQty(id, delta) {
  const item = medicineOrderState.find((m) => m.id === id);
  if (item) { item.quantity = Math.max(1, item.quantity + delta); renderChecklistUI(); }
}

function toggleFulfillmentType(type) {
  selectedFulfillment = type;
  $("homeDeliveryLocationBox").classList.toggle("hidden", type !== "home_delivery");
  if (type !== "home_delivery") loadSanjeevaniPickupBranches({ selectId: "pickupCounterSelect", address: $("finalDeliveryAddress").value });
  $("selfPickupLocationBox").classList.toggle("hidden", type === "home_delivery");
  $("labelHomeDelivery").classList.toggle("is-active", type === "home_delivery");
  $("labelPickup").classList.toggle("is-active", type !== "home_delivery");
}

function locate(inputId, labelId, idleText) {
  const label = $(labelId);
  if (!navigator.geolocation) { notify("Location is not available", "Type the address instead.", "info"); return; }
  label.textContent = "Finding you...";
  navigator.geolocation.getCurrentPosition(async (pos) => {
    const { latitude: lat, longitude: lon } = pos.coords;
    const fallback = `Map pin ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
    try {
      const res = await fetch(`/api/v1/pickup/reverse-geocode?lat=${lat}&lon=${lon}`);
      $(inputId).value = res.ok ? ((await res.json()).display_name || fallback) : fallback;
    } catch (e) {
      $(inputId).value = fallback;
    } finally {
      label.textContent = "Location added";
      setTimeout(() => { label.textContent = idleText; }, 3000);
    }
  }, () => {
    notify("Couldn't get your location", "Allow location access in the browser, or type the address.", "info");
    label.textContent = idleText;
  }, { timeout: 8000 });
}
function detectCurrentLocation() { locate("finalDeliveryAddress", "detectLocationText", "Use my location"); }
function detectStoreGPSLocation() { locate("storeDeliveryAddressInput", "storeDetectLocationText", "Use my location"); }

function fillConfirmSheet(rows, heading, destination) {
  $("modalOrderItemsList").innerHTML = rows.map((r) => `
    <div class="cart-line"><span class="prod-ico ${r.cls}" style="width:36px;height:36px;border-radius:10px">${icon(r.ico)}</span>
      <div class="t"><b>${esc(r.name)}</b><span>${esc(r.detail)}</span></div></div>`).join("");
  $("modalFulfillmentHeading").innerText = heading;
  $("modalFinalDestinationText").innerText = destination;
  $("pharmacyConfirmModal").classList.remove("hidden");
}

function openPharmacyConfirmModal() {
  const items = medicineOrderState.filter((i) => i.isSelected);
  if (!items.length) { notify("Nothing selected", "Tick at least one item to order.", "info"); return; }
  const home = selectedFulfillment === "home_delivery";
  fillConfirmSheet(items.map((i) => ({ name: i.drug_name, detail: `${i.quantity} × ${i.unit}`, cls: i.isDevice ? "cat-rpm" : "cat-rx", ico: i.isDevice ? "watch" : "pill" })),
    home ? "Delivery to" : "Pick up at",
    home ? ($("finalDeliveryAddress").value.trim() || "Address on file") : $("pickupCounterSelect").value);
}

function closePharmacyConfirmModal() { $("pharmacyConfirmModal").classList.add("hidden"); }

async function executeConfirmedOrder() {
  closePharmacyConfirmModal();
  const orderRef = "SNJ-RX-" + Math.floor(1000 + Math.random() * 9000);
  const patName = $("patient_name")?.value.trim() || "Patient";
  let orderedItems, destination, fulfillment;

  if (currentTab === "pharmacy") {
    orderedItems = pharmacyCart.map((i) => ({ id: i.id, name: i.name, price_aed: i.price, quantity: i.quantity, is_device: i.category.includes("RPM"), category: i.category }));
    fulfillment = storeFulfillmentMode;
    destination = fulfillment === "home_delivery"
      ? ($("storeDeliveryAddressInput").value.trim() || "Al Reem Island, Abu Dhabi")
      : $("storePickupCounterSelect").value;
    $("pharmacyStoreOrderSuccess").classList.remove("hidden");
    $("pharmacyStoreSuccessTitle").innerText = fulfillment === "home_delivery" ? `Order ${orderRef} is on its way` : `Order ${orderRef} will be ready in 30 minutes`;
    $("pharmacyStoreSuccessDetail").innerText = fulfillment === "home_delivery"
      ? `Delivering to ${destination}, in about 2-3 hours.` : `Collect it at ${destination}. Show ${orderRef} at the counter.`;
    window.scrollTo({ top: 0, behavior: "smooth" });
    clearPharmacyCart();
  } else {
    orderedItems = medicineOrderState.filter((i) => i.isSelected).map((i) => ({ id: i.id, name: i.drug_name, price_aed: i.price_aed || 25, quantity: i.quantity, is_device: !!i.isDevice, category: i.isDevice ? "RPM Device" : "Prescription" }));
    fulfillment = selectedFulfillment;
    destination = fulfillment === "home_delivery" ? ($("finalDeliveryAddress").value.trim() || "Address on file") : $("pickupCounterSelect").value;
    $("pharmacyAlertBox").classList.remove("hidden");
    $("orderSuccessTitle").innerText = fulfillment === "home_delivery" ? `Order ${orderRef} is on its way` : `Order ${orderRef} will be ready in 30 minutes`;
    $("orderSuccessDetail").innerText = fulfillment === "home_delivery"
      ? `A courier is taking it to ${destination}. It should arrive in 2-3 hours.` : `Collect it at ${destination}. Show ${orderRef} at the counter.`;
    $("pharmacyAlertBox").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  try {
    const response = await fetch("/api/v1/orders/refill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patient_name: patName, order_ref: orderRef, fulfillment_mode: fulfillment, destination, items: orderedItems }),
    });
    if (response.ok) {
      const data = await response.json();
      if (data.telemetry) applyLiveTelemetry(data.telemetry, data.order_flag, data.flag_label);
    }
  } catch (err) {
    console.warn("Order tracking failed:", err);
  }
}

// ---------------------------------------------------------------- doctors
let docCall = null;

function renderDoctors() {
  const search = ($("doctorSearchInput")?.value || "").toLowerCase();
  const dept = $("deptSelect")?.value || "all";
  const list = doctorsData.filter((d) => (dept === "all" || d.dept === dept)
    && [d.name, d.title, d.hospital].some((s) => s.toLowerCase().includes(search)));
  $("doctorsGrid").innerHTML = list.length ? list.map((doc, i) => `
    <article class="dcard">
      <div style="display:flex;gap:14px;align-items:center">
        <span class="avatar avatar-lg" style="background:${AVATAR_COLORS[(doc.id - 1) % AVATAR_COLORS.length]}">${initials(doc.name)}</span>
        <div><h3>${esc(doc.name)}</h3><p class="sub">${esc(doc.title)}</p></div>
      </div>
      <div class="meta">
        <span>${icon("location")}${esc(doc.hospital)}</span>
        <span>${icon("star")}${doc.rating} <span class="muted">(${doc.reviews} reviews)</span></span>
        <span>${icon("clock")}Next opening: <b style="font-weight:500">${esc(doc.available)}</b></span>
      </div>
      <div class="foot-actions">
        <button class="btn btn-dark btn-block" onclick="openApptSheet({doctor: '${esc(doc.name.toLowerCase())}'})"><i data-icon="calendar"></i> Book a visit</button>
      </div>
    </article>`).join("")
    : `<div class="empty">${icon("search")}<p>No doctors match. Try another name or specialty.</p></div>`;
}
function filterDoctors() { renderDoctors(); }

function updateDocCallTargetChip() {
  const chip = $("docCallTargetChip");
  if (!chip) return;
  const patientName = ($("patient_name")?.value || (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.patient_name) || "Sonika").trim();
  const phone = ($("mobile_number")?.value || (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.mobile_number) || "+919380361678").trim();
  chip.textContent = `Target: ${patientName} (${phone})`;
}

async function triggerDoctorVoiceCall(doctorName, doctorTitle, hospital, available) {
  const patientId = ($("patient_id")?.value || (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.patient_id) || "SNJ-98214").trim();
  const patientName = ($("patient_name")?.value || (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.patient_name) || "Sonika").trim();
  const phone = ($("mobile_number")?.value || (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.mobile_number) || "+919380361678").trim();

  updateDocCallTargetChip();

  const panel = $("docCallPanel");
  if (panel) {
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
    if ($("docCallText")) $("docCallText").textContent = `Placing call to ${phone} for ${doctorName}...`;
    setDocCallStage("calling");
    if ($("docCallSummaryBox")) $("docCallSummaryBox").hidden = true;
    if ($("docCallSummary")) $("docCallSummary").hidden = true;
    if ($("docCallTranscript")) $("docCallTranscript").innerHTML = "";
    if ($("docCallTimer")) $("docCallTimer").textContent = "00:00";
    if ($("btnEndOrDismissDocCall")) {
      $("btnEndOrDismissDocCall").textContent = "End call";
      $("btnEndOrDismissDocCall").className = "btn btn-sm btn-danger";
    }
  }

  notify("Placing voicebot call", `Calling ${phone} (${patientName}) to book with ${doctorName}...`, "info");

  // Ensure patient record is upserted into Voicebot directory before call
  try {
    const parts = patientName.split(/\s+/);
    const firstName = parts[0] || patientName;
    const lastName = parts.slice(1).join(" ") || "";
    await fetch("/api/v1/voice/patients/upsert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        external_id: patientId,
        phone_e164: phone,
        first_name: firstName,
        last_name: lastName || undefined,
        preferred_language: "en",
        consent_calls: true
      })
    });
  } catch (err) {
    console.warn("Pre-call patient auto-upsert notice:", err);
  }

  try {
    const res = await fetch("/api/v1/voice/book-appointment-call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        doctor_name: doctorName,
        doctor_title: doctorTitle,
        hospital: hospital,
        slot: available,
        patient_id: patientId,
        patient_name: patientName,
        phone_number: phone
      })
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || "Call could not be placed");

    stopDocCall();
    docCall = { id: d.call_id, started: Date.now() };
    docCall.timer = setInterval(() => {
      const s = Math.floor((Date.now() - docCall.started) / 1000);
      if ($("docCallTimer")) $("docCallTimer").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }, 1000);
    pollDocCall();
  } catch (err) {
    stopDocCall();
    if (panel) panel.hidden = true;
    notify("Could not place call", err.message, "warn");
  }
}

function setDocCallStage(stage) {
  const order = ["calling", "ringing", "in_call", "ended"];
  const at = order.indexOf(stage);
  document.querySelectorAll("#docCallSteps li").forEach((li) => {
    const i = order.indexOf(li.dataset.stage);
    li.className = i < at || (stage === "ended" && i <= at) ? "done" : i === at ? "now" : "";
  });
  const wave = $("docCallWave");
  if (wave) {
    wave.hidden = (stage !== "in_call");
  }
  const btn = $("btnEndOrDismissDocCall");
  if (btn) {
    if (stage === "ended") {
      btn.textContent = "Dismiss";
      btn.className = "btn btn-sm btn-light";
    } else {
      btn.textContent = "End call";
      btn.className = "btn btn-sm btn-danger";
    }
  }
}

async function pollDocCall() {
  if (!docCall) return;
  let d;
  try {
    const res = await fetch(`/api/v1/voice/calls/${encodeURIComponent(docCall.id)}`);
    d = await res.json();
    if (!res.ok) throw new Error(d.detail || "status check failed");
  } catch (_) {
    if (docCall) docCall.poll = setTimeout(pollDocCall, 4000);
    return;
  }
  if (!docCall) return;

  if (d.stage && d.stage !== "unknown") setDocCallStage(d.stage);
  const stageLabels = {
    calling: "Placing the call",
    ringing: "Ringing your phone",
    in_call: "On the call with appointment desk",
    ended: "Call ended & appointment recorded",
  };
  if ($("docCallText")) {
    $("docCallText").textContent = stageLabels[d.stage] || $("docCallText").textContent;
  }

  if (d.transcript && d.transcript.length) {
    const transcriptEl = $("docCallTranscript");
    if (transcriptEl) {
      const html = d.transcript.map((t) =>
        `<div class="msg ${t.from === "agent" ? "agent" : "patient"}"><b>${t.from === "agent" ? "Appointment Desk" : "Patient"}:</b> <span>${esc(t.text)}</span></div>`
      ).join("");
      if (transcriptEl.innerHTML !== html) {
        transcriptEl.innerHTML = html;
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
      }
    }
  }

  if (d.done || d.stage === "ended") {
    if (docCall.timer) clearInterval(docCall.timer);
    if (docCall.poll) clearTimeout(docCall.poll);
    setDocCallStage("ended");
    if ($("docCallText")) $("docCallText").textContent = "Call ended & appointment recorded";
    if (d.summary) {
      if ($("docCallSummaryBox")) $("docCallSummaryBox").hidden = false;
      if ($("docCallSummary")) {
        $("docCallSummary").hidden = false;
        $("docCallSummary").textContent = d.summary;
      }
    }
    notify("Appointment call complete", "Patient record and booking details updated.", "good");

    // Force reload in Patient view and auto-refresh if Patient tab is loaded
    recordLoadedFor = null;
    const patId = ($("patient_id")?.value || (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.patient_id) || "SNJ-98214").trim();
    const phone = ($("mobile_number")?.value || (latestApiResponse && latestApiResponse.patient_context && latestApiResponse.patient_context.mobile_number) || "+919380361678").trim();
    if ($("recordQuery") && (patId || phone)) $("recordQuery").value = patId || phone;
    if (currentTab === "patient" && typeof loadPatientRecord === "function") {
      loadPatientRecord().catch(() => {});
    }

    docCall = null;
    return;
  }

  docCall.poll = setTimeout(pollDocCall, 2500);
}

function stopDocCall() {
  if (!docCall) return;
  if (docCall.poll) clearTimeout(docCall.poll);
  if (docCall.timer) clearInterval(docCall.timer);
  docCall = null;
  const wave = $("docCallWave");
  if (wave) wave.hidden = true;
}

function endOrDismissDocCall() {
  if (docCall) {
    stopDocCall();
    setDocCallStage("ended");
    if ($("docCallText")) $("docCallText").textContent = "Call ended";
    if ($("btnEndOrDismissDocCall")) {
      $("btnEndOrDismissDocCall").textContent = "Dismiss";
      $("btnEndOrDismissDocCall").className = "btn btn-sm btn-light";
    }
  } else {
    const panel = $("docCallPanel");
    if (panel) panel.hidden = true;
  }
}

function triggerDoctorVoiceCallFromPlan() {
  const followUp =
    latestApiResponse?.journey_result?.clinical_summary?.recommended_follow_up ||
    latestApiResponse?.ai_clinical_summary?.recommended_follow_up ||
    latestApiResponse?.ai_next_best_actions?.doctor_revisit_schedule?.purpose ||
    "General Medicine";

  const doctorNameInSummary =
    latestApiResponse?.journey_result?.clinical_summary?.doctor_name ||
    latestApiResponse?.ai_clinical_summary?.doctor_name;

  let targetDoc = null;
  if (doctorNameInSummary) {
    targetDoc = doctorsData.find((d) => d.name.toLowerCase().includes(doctorNameInSummary.toLowerCase()));
  }
  if (!targetDoc && followUp) {
    const fLower = String(followUp).toLowerCase();
    targetDoc = doctorsData.find((d) =>
      fLower.includes(d.dept.toLowerCase()) ||
      d.dept.toLowerCase().includes(fLower) ||
      d.title.toLowerCase().includes(fLower) ||
      fLower.includes(d.name.toLowerCase())
    );
  }
  if (!targetDoc) {
    targetDoc = doctorsData[0] || {
      name: "Dr. Karim Haddad",
      title: "Consultant Gastroenterologist",
      hospital: "Sanjeevani Medical City",
      available: "Sunday 1 Nov, 9:00 AM"
    };
  }

  switchTab("doctors");
  triggerDoctorVoiceCall(targetDoc.name, targetDoc.title, targetDoc.hospital, targetDoc.available);
}

window.triggerDoctorVoiceCall = triggerDoctorVoiceCall;
window.triggerDoctorVoiceCallFromPlan = triggerDoctorVoiceCallFromPlan;
window.endOrDismissDocCall = endOrDismissDocCall;
window.pollDocCall = pollDocCall;
window.stopDocCall = stopDocCall;
window.setDocCallStage = setDocCallStage;
window.updateDocCallTargetChip = updateDocCallTargetChip;

async function bookDoctorDirect(doctorName, specialty, when) {
  const patientId = ($("patient_id")?.value || "SNJ-98214").trim();
  const patientName = ($("patient_name")?.value || "Sonika").trim();
  const phone = ($("mobile_number")?.value || "+919380361678").trim();

  try {
    const res = await fetch("/api/v1/voice/book-appointment-direct", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        doctor_name: doctorName,
        specialty: specialty || "Specialist Consultation",
        booked_slot: when || "Upcoming slot",
        patient_id: patientId,
        patient_name: patientName,
        phone_number: phone,
        reason: "General Consultation"
      })
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || "Booking failed");
    recordLoadedFor = null;
    notify(`Appointment booked! (${d.external_ref || 'Confirmed'})`, `Scheduled with ${doctorName} on ${when || 'upcoming slot'}. Details added to Patient records.`, "success");
    switchTab("patient");
  } catch (err) {
    notify("Booking failed", err.message, "warn");
  }
}

function bookDoctorConsultation(doctorName, when) {
  bookDoctorDirect(doctorName, "General Consultation", when);
}

// ---------------------------------------------------------------- labs
function renderLabs() {
  const search = ($("labSearchInput")?.value || "").toLowerCase();
  const list = labsData.filter((l) => l.name.toLowerCase().includes(search) || l.category.toLowerCase().includes(search));
  $("labsGrid").innerHTML = list.length ? list.map((lab) => `
    <article class="dcard">
      <div class="row-between"><span class="chip">${esc(lab.category)}</span><span class="price">${esc(lab.price)}</span></div>
      <h3>${esc(lab.name)}</h3>
      <div class="meta">
        <span>${icon("clock")}Results: ${esc(lab.time)}</span>
        <span>${icon("info")}${esc(lab.fasting)}</span>
        <span>${icon(lab.home ? "home" : "building")}${lab.home ? "Home sample collection available" : "At a Sanjeevani hospital only"}</span>
      </div>
      <div class="foot-actions"><button class="btn ${lab.home ? "btn-primary" : "btn-secondary"} btn-block" onclick="bookLabTest('${esc(lab.name)}', ${lab.home})">${lab.home ? "Book home collection" : "Book at hospital"}</button></div>
    </article>`).join("")
    : `<div class="empty">${icon("search")}<p>No tests match.</p></div>`;
}
function filterLabs() { renderLabs(); }
function bookLabTest(testName, home) {
  notify(`${testName} booked`, home ? "A Sanjeevani phlebotomist will come to your address to take the sample." : "We will confirm your appointment by SMS.");
}

// ---------------------------------------------------------------- pharmacy store
let pharmacyCart = [];
let activePharmacyCategory = "all";
let storeFulfillmentMode = "home_delivery";

function setPharmacyCategory(cat) {
  activePharmacyCategory = cat;
  document.querySelectorAll("#pharmacyCategoryPills button").forEach((b) => b.classList.toggle("is-active", b.id === `cat-${cat}`));
  renderPharmacyProducts();
}
function filterPharmacyProducts() { renderPharmacyProducts(); }

function renderPharmacyProducts() {
  const grid = $("pharmacyProductsGrid");
  if (!grid) return;
  const search = ($("pharmacySearchInput")?.value || "").toLowerCase().trim();
  const list = pharmacyProductsData.filter((p) => (activePharmacyCategory === "all" || p.category.includes(activePharmacyCategory) || (activePharmacyCategory === "Prescription" && p.badge === "Prescription"))
    && [p.name, p.desc, p.category].some((s) => s.toLowerCase().includes(search)));
  if (!list.length) {
    grid.innerHTML = `<div class="empty">${icon("pill")}<p>Nothing matches. Try a generic name such as paracetamol, or choose All.</p></div>`;
    return;
  }
  grid.innerHTML = list.map((p) => {
    const inCart = pharmacyCart.find((i) => i.id === p.id);
    const look = productLook(p);
    return `
    <article class="dcard">
      <div style="display:flex;gap:14px;align-items:flex-start">
        <span class="prod-ico ${look.cls}">${icon(look.ico)}</span>
        <div style="flex:1;min-width:0"><span class="chip" style="margin-bottom:6px">${esc(p.badge)}</span><h3>${esc(p.name)}</h3></div>
      </div>
      <p class="small muted">${esc(p.desc)}</p>
      <div class="row-between foot-actions"><span class="price">AED ${p.price}</span><span class="small muted">${esc(p.unit)}</span></div>
      ${inCart
        ? `<div class="incart"><span>In your bag</span>${stepper(`adjustPharmacyCartQty(${p.id}, -1)`, `adjustPharmacyCartQty(${p.id}, 1)`, inCart.quantity, `Quantity of ${p.name}`)}</div>`
        : `<button class="btn btn-secondary btn-block" onclick="addToPharmacyStoreCart(${p.id})">${icon("plus")}Add to bag</button>`}
    </article>`;
  }).join("");
}

function addToPharmacyStoreCart(productId) {
  const p = pharmacyProductsData.find((x) => x.id === productId);
  if (!p) return;
  const existing = pharmacyCart.find((i) => i.id === productId);
  if (existing) existing.quantity += 1;
  else pharmacyCart.push({ id: p.id, name: p.name, price: p.price, unit: p.unit, category: p.category, quantity: 1 });
  renderPharmacyProducts();
  renderPharmacyCartUI();
}

function adjustPharmacyCartQty(productId, delta) {
  const item = pharmacyCart.find((i) => i.id === productId);
  if (!item) return;
  item.quantity += delta;
  if (item.quantity <= 0) pharmacyCart = pharmacyCart.filter((i) => i.id !== productId);
  renderPharmacyProducts();
  renderPharmacyCartUI();
}

function removePharmacyCartItem(productId) {
  pharmacyCart = pharmacyCart.filter((i) => i.id !== productId);
  renderPharmacyProducts();
  renderPharmacyCartUI();
}

function clearPharmacyCart() {
  pharmacyCart = [];
  renderPharmacyProducts();
  renderPharmacyCartUI();
}

function renderPharmacyCartUI() {
  const box = $("pharmacyCartItemsContainer");
  if (!box) return;
  const count = pharmacyCart.reduce((s, i) => s + i.quantity, 0);
  $("cartItemsCountText").innerText = `${count} ${count === 1 ? "item" : "items"}`;
  $("btnStoreCheckout").classList.toggle("is-disabled", !pharmacyCart.length);
  if (!pharmacyCart.length) {
    box.innerHTML = `<p class="small muted" style="padding:18px 0;text-align:center">Your bag is empty.</p>`;
    $("cartSubtotalText").innerText = "AED 0.00";
    $("cartDeliveryFeeText").innerText = "Free";
    $("cartGrandTotalText").innerText = "AED 0.00";
    return;
  }
  let subtotal = 0;
  box.innerHTML = pharmacyCart.map((item) => {
    const total = item.price * item.quantity;
    subtotal += total;
    const look = productLook(item);
    return `<div class="cart-line">
      <span class="prod-ico ${look.cls}" style="width:36px;height:36px;border-radius:10px">${icon(look.ico)}</span>
      <div class="t"><b>${esc(item.name)}</b><span class="num">AED ${item.price} × ${item.quantity}</span></div>
      ${stepper(`adjustPharmacyCartQty(${item.id}, -1)`, `adjustPharmacyCartQty(${item.id}, 1)`, item.quantity, `Quantity of ${item.name}`)}
    </div>`;
  }).join("");
  // Delivery is free over AED 50 or for pickup, otherwise AED 15.
  const fee = storeFulfillmentMode === "self_pickup" || subtotal >= 50 ? 0 : 15;
  $("cartSubtotalText").innerText = `AED ${subtotal.toFixed(2)}`;
  $("cartDeliveryFeeText").innerText = fee === 0 ? "Free" : `AED ${fee.toFixed(2)}`;
  $("cartGrandTotalText").innerText = `AED ${(subtotal + fee).toFixed(2)}`;
}

function setStoreFulfillment(mode) {
  storeFulfillmentMode = mode;
  $("storeHomeAddressBox").classList.toggle("hidden", mode !== "home_delivery");
  $("storePickupBox").classList.toggle("hidden", mode === "home_delivery");
  if (mode !== "home_delivery") loadSanjeevaniPickupBranches({ selectId: "storePickupCounterSelect", address: $("storeDeliveryAddressInput").value });
  $("storeLabelHomeDelivery").classList.toggle("is-active", mode === "home_delivery");
  $("storeLabelPickup").classList.toggle("is-active", mode !== "home_delivery");
  renderPharmacyCartUI();
}

function openStoreOrderConfirmModal() {
  if (!pharmacyCart.length) { notify("Your bag is empty", "Add something first.", "info"); return; }
  const home = storeFulfillmentMode === "home_delivery";
  fillConfirmSheet(pharmacyCart.map((i) => { const l = productLook(i); return { name: i.name, detail: `${i.quantity} × AED ${i.price}`, cls: l.cls, ico: l.ico }; }),
    home ? "Delivery to" : "Pick up at",
    home ? ($("storeDeliveryAddressInput").value.trim() || $("finalDeliveryAddress")?.value.trim() || "Address on file (Al Reem Island, Abu Dhabi)") : $("storePickupCounterSelect").value);
}

// ---------------------------------------------------------------- insights
let liveTelemetry = { total_pharmacy_orders: 3420, order_breakdown: { medicine_only: 2180, rpm_only: 410, combo_meds_and_rpm: 830 }, avg_order_value_aed: 285.0 };

async function fetchAnalyticsTelemetry() {
  try {
    const res = await fetch("/api/v1/analytics/telemetry");
    if (res.ok) {
      const data = await res.json();
      if (data.telemetry) applyLiveTelemetry(data.telemetry);
    }
  } catch (e) {
    console.warn("Could not fetch telemetry:", e);
  }
}

function applyLiveTelemetry(telemetry) {
  liveTelemetry = telemetry;
  const total = $("kpiPharmacyOrdersCount");
  if (total) {
    total.innerText = Number(telemetry.total_pharmacy_orders).toLocaleString();
    total.classList.add("bump");
    setTimeout(() => total.classList.remove("bump"), 1200);
  }
  if (telemetry.order_breakdown) {
    $("kpiMedsCount").innerText = Number(telemetry.order_breakdown.medicine_only).toLocaleString();
    $("kpiRpmCount").innerText = Number(telemetry.order_breakdown.rpm_only).toLocaleString();
    $("kpiComboCount").innerText = Number(telemetry.order_breakdown.combo_meds_and_rpm).toLocaleString();
  }
  if (telemetry.avg_order_value_aed) $("kpiAvgOrderValue").innerText = `AED ${Math.round(telemetry.avg_order_value_aed)}`;
  if (telemetry.recent_events) renderLiveEventsFeed(telemetry.recent_events);
  if (chartInstances.funnel) {
    chartInstances.funnel.data.datasets[0].data[3] = telemetry.total_pharmacy_orders;
    chartInstances.funnel.update();
  }
}

const EVENT_LOOK = {
  COMBO_MEDS_AND_RPM: { ico: "cart", cls: "cat-kids", label: "Medicines and device" },
  RPM_ONLY: { ico: "watch", cls: "cat-rpm", label: "Device" },
  MEDICINE_ONLY: { ico: "pill", cls: "cat-rx", label: "Medicines" },
  VOICE_AI: { ico: "mic", cls: "cat-otc", label: "Question answered" },
};

function renderLiveEventsFeed(events) {
  const feed = $("analyticsLiveEvents");
  if (!feed) return;
  feed.innerHTML = events.map((evt) => {
    const look = EVENT_LOOK[evt.flag] || EVENT_LOOK.VOICE_AI;
    return `<div class="event"><span class="prod-ico ${look.cls}" style="width:36px;height:36px;border-radius:10px">${icon(look.ico)}</span>
      <div class="t"><b>${esc(evt.title)}</b> <span>${esc(evt.desc)}</span></div>
      <span class="chip" style="flex:none">${look.label}</span><time>${esc(evt.time)}</time></div>`;
  }).join("");
}

// Chart colours: one categorical order, neutral grid, system font.
const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#7a4bb5", "#a1a1a6"];

function initAnalyticsCharts() {
  fetchAnalyticsTelemetry();
  if (!window.Chart) { setTimeout(initAnalyticsCharts, 200); return; }
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = "#6e6e73";
  const legend = Chart.defaults.plugins.legend.labels;
  if (!legend.filled) {
    const base = legend.generateLabels;
    legend.generateLabels = (chart) => base(chart).map((l) => ({ ...l, fillStyle: l.strokeStyle, lineWidth: 0 }));
    legend.filled = true;
  }
  legend.usePointStyle = true;
  legend.pointStyle = "circle";
  legend.boxWidth = 7;
  legend.boxHeight = 7;
  legend.padding = 14;
  Chart.defaults.plugins.tooltip.backgroundColor = "rgba(29,29,31,.92)";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 10;
  const grid = { color: "rgba(0,0,0,.06)", drawTicks: false };
  const axes = { y: { beginAtZero: true, grid, border: { display: false } }, x: { grid: { display: false }, border: { display: false } } };

  if (!chartInstances.voiceCalls) {
    chartInstances.voiceCalls = new Chart($("chartVoiceCalls"), {
      type: "line",
      data: {
        labels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        datasets: [
          { label: "Received", data: [1840, 2120, 2450, 2300, 2800, 1950, 1360], borderColor: PALETTE[0], backgroundColor: "rgba(42,120,214,.08)", fill: true, tension: .35, borderWidth: 2, pointRadius: 0, pointHoverRadius: 5 },
          { label: "Answered", data: [1620, 1890, 2210, 2080, 2520, 1740, 1220], borderColor: PALETTE[2], borderWidth: 2, tension: .35, pointRadius: 0, pointHoverRadius: 5 },
        ],
      },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { position: "top", align: "end" } }, scales: axes },
    });
  }
  if (!chartInstances.departments) {
    chartInstances.departments = new Chart($("chartDepartments"), {
      type: "bar",
      data: { labels: ["Endocrinology", "Gastroenterology", "Paediatrics", "Cardiology", "Oncology", "Other"],
              datasets: [{ label: "Share (%)", data: [32, 24, 18, 14, 8, 4], backgroundColor: PALETTE[0], borderRadius: 6, maxBarThickness: 22 }] },
      options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.raw}%` } } },
                 scales: { x: { beginAtZero: true, grid, border: { display: false }, ticks: { callback: (v) => v + "%" } }, y: { grid: { display: false }, border: { display: false } } } },
    });
  }
  if (!chartInstances.funnel) {
    chartInstances.funnel = new Chart($("chartFunnel"), {
      type: "bar",
      data: { labels: ["Prescribed", "Plan sent", "Order set up", "Delivered"],
              datasets: [{ label: "Patients", data: [4480, 4120, 3620, liveTelemetry.total_pharmacy_orders], backgroundColor: ["#c9dcf4", "#8db6e9", "#4f8fde", PALETTE[0]], borderRadius: 6, maxBarThickness: 56 }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: axes },
    });
  }
  if (!chartInstances.sentiment) {
    chartInstances.sentiment = new Chart($("chartSentiment"), {
      type: "bar",
      data: { labels: ["5 stars", "4 stars", "3 stars", "1-2 stars"],
              datasets: [{ label: "Share (%)", data: [82, 15, 2.4, 0.6], backgroundColor: [PALETTE[2], "#7fd3b1", PALETTE[3], PALETTE[1]], borderRadius: 6, maxBarThickness: 56 }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.raw}%` } } },
                 scales: { ...axes, y: { ...axes.y, ticks: { callback: (v) => v + "%" } } } },
    });
  }
}

// ---------------------------------------------------------------- start
document.addEventListener("DOMContentLoaded", async () => {
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  document.querySelectorAll("[data-rx-mode]").forEach((b) => b.addEventListener("click", () => setRxMode(b.dataset.rxMode)));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePharmacyConfirmModal(); });
  window.addEventListener("hashchange", () => switchTab(location.hash.slice(1)));
  renderPharmacyCartUI();
  fetchAnalyticsTelemetry();
  await initVoiceConnection();
  switchTab(location.hash.slice(1) || "care-journey");
});

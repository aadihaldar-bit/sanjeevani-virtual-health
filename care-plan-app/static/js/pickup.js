// Nearby pharmacy finder (Pharmacy view) and delivery address suggestions.
// The map is MapLibre on OpenFreeMap tiles (OpenStreetMap data, no key); search goes through /api/v1/pickup.
"use strict";

let pickupMap = null;
let pickupOriginMarker = null;
let pickupMarkers = [];
let pickupOrigin = null;
let pickupSearchId = 0;
let pickupSearchAbortController = null;

const ACCENT = "#0a62d0";

function initialisePickupMap() {
  if (pickupMap || typeof maplibregl === "undefined") return;
  pickupMap = new maplibregl.Map({
    container: "pickupMap",
    style: "https://tiles.openfreemap.org/styles/liberty",
    center: [54.3773, 24.4539],
    zoom: 12,
    attributionControl: { compact: true },
  });
  pickupMap.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
}

function clearPickupMarkers() {
  pickupMarkers.forEach((m) => m.remove());
  pickupMarkers = [];
  if (pickupMap && pickupMap.getLayer("pickup-route-line")) {
    pickupMap.removeLayer("pickup-route-line");
    pickupMap.removeSource("pickup-route-line");
  }
}

function setNearbyPharmacyStatus(message) { $("nearbyPharmacyStatus").textContent = message; }

function setNearbyPharmacyLoading(isLoading) {
  $("nearbyLocateButton").disabled = isLoading;
  document.querySelectorAll("#nearbyRadiusSeg button").forEach((b) => { b.disabled = isLoading; });
}

// ---------------------------------------------------------------- address suggestions (shared)
// Each address box gets a dropdown that lists matches while the person types: "Searching..." at once, then up to
// seven UAE addresses (place in bold, area underneath), arrow keys to move, Enter to choose, Escape to close.
const SUGGEST_MIN = 3, SUGGEST_DELAY_MS = 180;
const suggestCache = new Map();

async function fetchSuggestions(query) {
  const key = query.toLowerCase();
  if (suggestCache.has(key)) return suggestCache.get(key);
  const r = await fetch(`/api/v1/pickup/address-suggestions?q=${encodeURIComponent(query)}`);
  const list = (await r.json()).suggestions || [];
  suggestCache.set(key, list);
  return list;
}

function highlight(text, query) {
  const i = text.toLowerCase().indexOf(query.toLowerCase());
  return i < 0 ? esc(text) : `${esc(text.slice(0, i))}<mark>${esc(text.slice(i, i + query.length))}</mark>${esc(text.slice(i + query.length))}`;
}

function makeSuggester({ inputId, boxId, onPick, onEnterWithoutPick }) {
  const input = $(inputId), box = $(boxId);
  let timer = null, requestId = 0, items = [], active = -1;
  box.setAttribute("role", "listbox");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", boxId);

  const hide = () => { box.hidden = true; active = -1; input.setAttribute("aria-expanded", "false"); };
  const show = (html) => { box.innerHTML = html; box.hidden = false; input.setAttribute("aria-expanded", "true"); };
  const mark = () => box.querySelectorAll(".opt").forEach((b, i) => {
    b.classList.toggle("is-active", i === active);
    b.setAttribute("aria-selected", i === active);
    if (i === active) b.scrollIntoView({ block: "nearest" });
  });
  const pick = (i) => {
    const s = items[i];
    if (!s) return;
    input.value = s.display_name;
    hide();
    onPick(s);
  };

  function render(query) {
    if (!items.length) {
      show(`<div class="suggest-note">No UAE address matches “${esc(query)}”. Try a building, street or area.</div>`);
      return;
    }
    show(items.map((s, i) => `
      <button type="button" class="opt" role="option" data-i="${i}" id="${boxId}-${i}">
        ${icon("location")}<span><b>${highlight(s.line1 || s.display_name, query)}</b>${s.line2 ? `<small>${esc(s.line2)}</small>` : ""}</span>
      </button>`).join(""));
    box.querySelectorAll(".opt").forEach((b) => {
      b.addEventListener("mousedown", (e) => e.preventDefault());       // keep focus in the box while clicking
      b.addEventListener("click", () => pick(Number(b.dataset.i)));
    });
    active = -1;
  }

  async function load(query) {
    const id = ++requestId;
    try {
      const list = await fetchSuggestions(query);
      if (id !== requestId || input.value.trim() !== query) return;
      items = list;
      render(query);
    } catch (_) {
      if (id === requestId) show(`<div class="suggest-note">Address search isn't available right now.</div>`);
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    items = [];
    if (q.length < SUGGEST_MIN) { hide(); return; }
    const cached = suggestCache.get(q.toLowerCase());
    if (cached) { items = cached; render(q); return; }
    show(`<div class="suggest-note"><span class="mini-spin"></span>Searching addresses...</div>`);
    timer = setTimeout(() => load(q), SUGGEST_DELAY_MS);
  });
  input.addEventListener("focus", () => { if (items.length && input.value.trim().length >= SUGGEST_MIN) render(input.value.trim()); });
  input.addEventListener("blur", () => setTimeout(hide, 120));
  input.addEventListener("keydown", (e) => {
    const open = !box.hidden && items.length;
    if (e.key === "ArrowDown" && open) { e.preventDefault(); active = (active + 1) % items.length; mark(); }
    else if (e.key === "ArrowUp" && open) { e.preventDefault(); active = (active - 1 + items.length) % items.length; mark(); }
    else if (e.key === "Escape") { hide(); }
    else if (e.key === "Enter" && input.tagName === "INPUT") {
      e.preventDefault();
      if (open) pick(active >= 0 ? active : 0);
      else if (onEnterWithoutPick) onEnterWithoutPick();
    }
  });
  return { hide, load: (q) => load(q) };
}

let finderSuggest = null, storeSuggest = null, carePlanSuggest = null;

// ---------------------------------------------------------------- finder search box
function hideAddressSuggestions() { finderSuggest && finderSuggest.hide(); }
async function loadAddressSuggestions(query) { if (finderSuggest) await finderSuggest.load(query); }

async function searchNearbyAddress() {
  const input = $("nearbyAddress");
  const address = input.value.trim();
  if (address.length < 3) {
    setNearbyPharmacyStatus("Enter a street, area, city or landmark.");
    input.focus();
    return;
  }
  $("nearbyAddressButton").disabled = true;
  setNearbyPharmacyStatus("Finding that address...");
  try {
    const r = await fetch(`/api/v1/pickup/geocode?address=${encodeURIComponent(address)}`);
    const loc = await r.json();
    if (!r.ok) {
      await loadAddressSuggestions(address);
      setNearbyPharmacyStatus("Pick one of the suggested addresses.");
      return;
    }
    input.value = loc.display_name;
    hideAddressSuggestions();
    await searchNearbyPharmacies(loc.lat, loc.lon);
  } catch (_) {
    await loadAddressSuggestions(address);
    setNearbyPharmacyStatus("Pick one of the suggested addresses.");
  } finally {
    $("nearbyAddressButton").disabled = false;
  }
}

// ---------------------------------------------------------------- delivery address in the bag
function hideStoreAddressSuggestions() { storeSuggest && storeSuggest.hide(); }
function hideCarePlanAddressSuggestions() { carePlanSuggest && carePlanSuggest.hide(); }

// Fills a pickup <select> with the Sanjeevani pharmacy counters, nearest first, from a position or an address.
// With nothing to go on the server ranks from Al Reem Island; if the request fails the options already in the page stay.
async function loadSanjeevaniPickupBranches({ selectId, lat, lon, address } = {}) {
  const select = $(selectId);
  if (!select) return;
  const params = new URLSearchParams();
  if (lat != null && lon != null) { params.set("lat", lat); params.set("lon", lon); }
  else if (address && address.trim()) params.set("address", address.trim());
  try {
    const res = await fetch(`/api/v1/pickup/sanjeevani-branches${params.toString() ? `?${params}` : ""}`);
    if (!res.ok) return;
    const branches = (await res.json()).branches || [];
    if (!branches.length) return;
    select.innerHTML = branches.map((b, i) => `<option value="${esc(b.full_value || `${b.name} - ${b.counter}`)}">${esc(b.name)}, ${b.distance_km} km${i === 0 ? " (nearest)" : ""}</option>`).join("");
  } catch (err) {
    console.warn("Sanjeevani pickup branches could not be loaded:", err);
  }
}
window.loadSanjeevaniPickupBranches = loadSanjeevaniPickupBranches;

async function loadStoreNearbyPharmacies(lat, lon) {
  const panel = $("storeNearbyPharmacies"), list = $("storeNearbyPharmaciesList");
  panel.hidden = false;
  list.innerHTML = `<p class="small muted">Finding pharmacies nearby...</p>`;
  try {
    const r = await fetch(`/api/v1/pickup/nearby-pharmacies?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&radius_m=4000`);
    const data = await r.json();
    if (!r.ok) throw new Error();
    const top = (data.pharmacies || []).slice(0, 3);
    list.innerHTML = top.length
      ? top.map((p) => `<div class="nearby-row"><span>${esc(p.name || "Pharmacy")}</span><b class="num">${esc(p.distance_km)} km</b></div>`).join("")
      : `<p class="small muted">No pharmacies found near this address.</p>`;
  } catch (_) {
    list.innerHTML = `<p class="small muted">Nearby pharmacies can't be shown right now.</p>`;
  }
}

// ---------------------------------------------------------------- the finder
async function searchNearbyPharmacies(lat, lon) {
  initialisePickupMap();
  if (!pickupMap) {
    setNearbyPharmacyStatus("The map could not load. Check your internet connection and reload the page.");
    return;
  }
  pickupOrigin = { lat, lon };
  clearPickupMarkers();
  if (pickupOriginMarker) pickupOriginMarker.remove();
  const dot = document.createElement("div");
  dot.className = "you-dot";
  pickupOriginMarker = new maplibregl.Marker({ element: dot })
    .setLngLat([lon, lat])
    .setPopup(new maplibregl.Popup({ offset: 14 }).setText("You are here"))
    .addTo(pickupMap);
  pickupMap.flyTo({ center: [lon, lat], zoom: 14 });
  setNearbyPharmacyStatus("Looking for pharmacies nearby...");
  $("nearbyPharmacyList").replaceChildren();

  const searchId = ++pickupSearchId;
  if (pickupSearchAbortController) pickupSearchAbortController.abort();
  pickupSearchAbortController = new AbortController();
  setNearbyPharmacyLoading(true);
  try {
    const radius = $("nearbyRadius").value;
    const r = await fetch(`/api/v1/pickup/nearby-pharmacies?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&radius_m=${encodeURIComponent(radius)}`,
                          { signal: pickupSearchAbortController.signal });
    const data = await r.json();
    if (searchId !== pickupSearchId) return;
    if (!r.ok) throw new Error(data.detail || "the search failed");
    renderNearbyPharmacies(data);
  } catch (e) {
    if (e.name === "AbortError" || searchId !== pickupSearchId) return;
    setNearbyPharmacyStatus(`Pharmacies could not be loaded: ${e.message}`);
  } finally {
    if (searchId === pickupSearchId) {
      pickupSearchAbortController = null;
      setNearbyPharmacyLoading(false);
    }
  }
}

function renderNearbyPharmacies(data) {
  const list = $("nearbyPharmacyList");
  list.replaceChildren();
  setNearbyPharmacyStatus(
    data.source && data.source.startsWith("offline-sample") ? "Live pharmacy data is unavailable right now, so sample locations are shown."
      : data.expanded_search_m ? `None within ${data.radius_m / 1000} km. Showing the nearest within ${data.expanded_search_m / 1000} km.`
      : `${data.count} ${data.count === 1 ? "pharmacy" : "pharmacies"} nearby.`);
  if (!data.count) {
    list.innerHTML = `<p class="small muted" style="padding:14px 0">None found here. Try a larger radius.</p>`;
    return;
  }
  data.pharmacies.forEach((p, i) => {
    const pin = document.createElement("div");
    pin.className = "pin";
    pin.textContent = i + 1;
    const marker = new maplibregl.Marker({ element: pin })
      .setLngLat([p.lon, p.lat])
      .setPopup(new maplibregl.Popup({ offset: 18 }).setHTML(`<b>${esc(p.name || "Pharmacy")}</b><br>${esc(p.distance_km)} km away`))
      .addTo(pickupMap);
    pickupMarkers.push(marker);

    const open = p.open_now === true ? ["Open now", "chip-good"] : p.open_now === false ? ["Closed", "chip-bad"] : ["Hours not listed", ""];
    const card = document.createElement("button");
    card.type = "button";
    card.className = "place";
    card.innerHTML = `
      <span class="place-n">${i + 1}</span>
      <span class="place-t">
        <b>${esc(p.name || "Pharmacy")}</b>
        ${p.address ? `<span>${esc(p.address)}</span>` : ""}
        <span class="place-meta"><span class="chip ${open[1]}">${open[0]}</span>${p.rating ? `<span class="chip">${icon("star")}${esc(p.rating)}</span>` : ""}</span>
      </span>
      <span class="place-d num">${esc(p.distance_km)} km</span>`;
    card.addEventListener("click", () => selectNearbyPharmacy(p, marker, card));
    list.appendChild(card);
  });
  const bounds = new maplibregl.LngLatBounds([pickupOrigin.lon, pickupOrigin.lat], [pickupOrigin.lon, pickupOrigin.lat]);
  data.pharmacies.forEach((p) => bounds.extend([p.lon, p.lat]));
  pickupMap.fitBounds(bounds, { padding: 48, maxZoom: 14 });
}

function selectNearbyPharmacy(p, marker, card) {
  document.querySelectorAll("#nearbyPharmacyList .place").forEach((c) => c.classList.remove("is-active"));
  card.classList.add("is-active");
  pickupMarkers.forEach((m) => m.getPopup().isOpen() && m.togglePopup());
  marker.togglePopup();
  pickupMap.flyTo({ center: [p.lon, p.lat], zoom: 15 });
  const draw = () => {
    if (pickupMap.getLayer("pickup-route-line")) {
      pickupMap.removeLayer("pickup-route-line");
      pickupMap.removeSource("pickup-route-line");
    }
    pickupMap.addSource("pickup-route-line", { type: "geojson", data: { type: "Feature", properties: {},
      geometry: { type: "LineString", coordinates: [[pickupOrigin.lon, pickupOrigin.lat], [p.lon, p.lat]] } } });
    pickupMap.addLayer({ id: "pickup-route-line", type: "line", source: "pickup-route-line",
                         paint: { "line-color": ACCENT, "line-width": 3, "line-dasharray": [2, 2] } });
  };
  if (pickupMap.isStyleLoaded()) draw(); else pickupMap.once("load", draw);
}

function locateNearbyPharmacies() {
  if (!navigator.geolocation) { setNearbyPharmacyStatus("This browser can't share your location. Search an address instead."); return; }
  setNearbyPharmacyStatus("Getting your location...");
  navigator.geolocation.getCurrentPosition(
    (pos) => searchNearbyPharmacies(pos.coords.latitude, pos.coords.longitude),
    () => setNearbyPharmacyStatus("Location access was blocked. Allow it in the browser, or search an address."),
    { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 });
}

function refreshNearbyPharmacies() {
  if (pickupOrigin) searchNearbyPharmacies(pickupOrigin.lat, pickupOrigin.lon);
}

document.addEventListener("DOMContentLoaded", () => {
  finderSuggest = makeSuggester({
    inputId: "nearbyAddress", boxId: "nearbyAddressSuggestions",
    onPick: (s) => searchNearbyPharmacies(s.lat, s.lon), onEnterWithoutPick: searchNearbyAddress,
  });
  storeSuggest = makeSuggester({
    inputId: "storeDeliveryAddressInput", boxId: "storeDeliveryAddressSuggestions",
    onPick: (s) => loadStoreNearbyPharmacies(s.lat, s.lon),
  });
  carePlanSuggest = makeSuggester({
    inputId: "finalDeliveryAddress", boxId: "carePlanAddressSuggestions",
    onPick: (s) => loadSanjeevaniPickupBranches({ selectId: "pickupCounterSelect", lat: s.lat, lon: s.lon }),
  });
  loadSanjeevaniPickupBranches({ selectId: "pickupCounterSelect" });
  loadSanjeevaniPickupBranches({ selectId: "storePickupCounterSelect" });
  document.querySelectorAll("#nearbyRadiusSeg button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#nearbyRadiusSeg button").forEach((x) => x.classList.toggle("is-active", x === b));
    $("nearbyRadius").value = b.dataset.radius;
    refreshNearbyPharmacies();
  }));
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".suggest-wrap")) { hideAddressSuggestions(); hideStoreAddressSuggestions(); hideCarePlanAddressSuggestions(); }
  });
  // The map needs a visible container to size itself; create it the first time the Pharmacy view opens.
  const openPharmacy = () => { if (currentTab === "pharmacy") { initialisePickupMap(); setTimeout(() => pickupMap && pickupMap.resize(), 0); } };
  window.addEventListener("hashchange", openPharmacy);
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", openPharmacy));
  window.addEventListener("load", openPharmacy);
});

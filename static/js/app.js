'use strict';

const DEFAULT_CENTER = [46.15, 14.65];
const DEFAULT_ZOOM = 8;
const map = L.map('map', { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: true, attributionControl: false });

const TILE_LAYERS = {
  dark: { label: 'Dark Matter', url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', maxZoom: 18 },
  dark_nolabels: { label: 'Dark No Labels', url: 'https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', maxZoom: 18 },
  voyager: { label: 'Voyager', url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', maxZoom: 19 },
  positron: { label: 'Positron', url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', maxZoom: 19 },
  esri_gray_dark: { label: 'Esri Dark Gray', url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', maxZoom: 16 },
  esri_sat: { label: 'Esri Satellite', url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', maxZoom: 18 },
  esri_topo: { label: 'Esri Topo', url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', maxZoom: 18 },
  stadia_outdoors: { label: 'Stadia Outdoors', url: 'https://tiles.stadiamaps.com/tiles/outdoors/{z}/{x}/{y}{r}.png', maxZoom: 20 },
  stamen_terrain: { label: 'Stamen Terrain', url: 'https://tiles.stadiamaps.com/tiles/stamen_terrain/{z}/{x}/{y}{r}.png', maxZoom: 18 },
};

const LAYER_LS_KEY = 'ais_base_layer';
const RING_LS_KEY = 'ais_rings';
let baseTileLayer = null;
let currentLayerKey = localStorage.getItem(LAYER_LS_KEY) || 'dark';
let vesselsData = {};
let historyData = {};
let markers = {};
let trails = {};
let rangeRings = [];
let selectedMmsi = null;
let followMmsi = null;
let showTrails = true;
let showRings = true;
let currentTab = 'active';
let receiverPos = null;
let receiverMarker = null;
let watchlist = loadWatchlist();
let alertRadiusNm = parseFloat(localStorage.getItem('ais_alert_radius_nm') || '2');

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmtSpd(v) { return v != null ? `${v} kt` : '—'; }
function fmtDist(v) { return v != null ? `${v} nm` : '—'; }
function fmtCourse(v) { return v != null ? `${v}°` : '—'; }
function fmtTcpa(v) { return v != null ? `${v} min` : '—'; }
function fmtAge(ts) {
  if (!ts) return '—';
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}
function vesselLabel(v) { return v.name || v.callsign || v.mmsi; }
function vesselCountry(v) { return [v.country_flag || null, v.country || null].filter(Boolean).join(' '); }
function fmtMeters(v) { return v != null ? `${v} m` : '—'; }
function loadWatchlist() {
  try { return new Set(JSON.parse(localStorage.getItem('ais_watchlist') || '[]')); } catch(e) { return new Set(); }
}
function saveWatchlist() { localStorage.setItem('ais_watchlist', JSON.stringify([...watchlist])); }
function isWatched(mmsi) { return watchlist.has(String(mmsi)); }
function isAlert(v) { return v.approaching && v.cpa_distance_nm != null && v.cpa_distance_nm <= alertRadiusNm; }
function vesselColor(v) {
  if (isAlert(v)) return '#ff5050';
  if (isWatched(v.mmsi)) return '#38bdf8';
  if (v.gone) return '#6f7f94';
  if (v.ship_type >= 80 && v.ship_type < 90) return '#f0c040';
  if (v.ship_type >= 60 && v.ship_type < 70) return '#38bdf8';
  if (v.ship_type === 35 || v.ship_type === 55) return '#ff8080';
  if ((v.speed || 0) > 20) return '#3ddc84';
  return '#e8b04f';
}

function setBaseLayer(key, offlineId) {
  if (baseTileLayer) { map.removeLayer(baseTileLayer); baseTileLayer = null; }
  if (offlineId) {
    baseTileLayer = L.tileLayer(`http://localhost:8092/services/${offlineId}/tiles/{z}/{x}/{y}`, { maxZoom: 16, tileSize: 256 }).addTo(map);
    currentLayerKey = 'offline:' + offlineId;
  } else {
    const def = TILE_LAYERS[key] || TILE_LAYERS.dark;
    baseTileLayer = L.tileLayer(def.url, { maxZoom: def.maxZoom }).addTo(map);
    currentLayerKey = key;
  }
  try { localStorage.setItem(LAYER_LS_KEY, currentLayerKey); } catch(e) {}
  renderLayerPicker();
}

function initBaseTiles() {
  const saved = localStorage.getItem(LAYER_LS_KEY) || 'dark';
  if (saved.startsWith('offline:')) {
    const id = saved.slice(8);
    baseTileLayer = L.tileLayer(`http://localhost:8092/services/${id}/tiles/{z}/{x}/{y}`, { maxZoom: 16, tileSize: 256 }).addTo(map);
    return;
  }
  const def = TILE_LAYERS[saved] || TILE_LAYERS.dark;
  baseTileLayer = L.tileLayer(def.url, { maxZoom: def.maxZoom }).addTo(map);
}

function renderLayerPicker() {
  const container = el('layer-picker');
  if (!container) return;
  let html = '';
  Object.entries(TILE_LAYERS).forEach(([key, def]) => {
    html += `<div class="layer-opt${currentLayerKey === key ? ' active' : ''}" onclick="setBaseLayer('${key}')">${def.label}</div>`;
  });
  html += '<div class="set-section" style="padding-top:6px">Offline</div>';
  html += '<div id="offline-layers-list"><div class="layer-opt" style="pointer-events:none;opacity:0.5">Loading…</div></div>';
  container.innerHTML = html;
  loadOfflineLayers();
}

async function loadOfflineLayers() {
  const listEl = el('offline-layers-list');
  if (!listEl) return;
  try {
    const ac = new AbortController();
    const tid = setTimeout(() => ac.abort(), 2000);
    const services = await (await fetch('http://localhost:8092/services', { signal: ac.signal })).json();
    clearTimeout(tid);
    if (!services.length) {
      listEl.innerHTML = '<div class="layer-opt" style="pointer-events:none;opacity:0.5">No offline maps in OPS-TOC</div>';
      return;
    }
    listEl.innerHTML = services.map(s => {
      const id = s.url.split('/').pop();
      const active = currentLayerKey === 'offline:' + id;
      return `<div class="layer-opt${active ? ' active' : ''}" onclick="setBaseLayer(null,'${id}')">${esc(s.name || id)}</div>`;
    }).join('');
  } catch(e) {
    listEl.innerHTML = '<div class="layer-opt" style="pointer-events:none;opacity:0.5">mbtileserver offline</div>';
  }
}

initBaseTiles();

map.on('dragstart', () => { if (followMmsi) { followMmsi = null; updateFollowBtn(); } });
const CenterControl = L.Control.extend({
  options: { position: 'topleft' },
  onAdd() {
    const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
    const btn = L.DomUtil.create('a', 'leaflet-center-btn', container);
    btn.innerHTML = '⊕'; btn.href = '#'; btn.title = 'Center on receiver';
    L.DomEvent.disableClickPropagation(btn);
    L.DomEvent.on(btn, 'click', e => { L.DomEvent.preventDefault(e); if (receiverPos?.lat) map.setView([receiverPos.lat, receiverPos.lon], map.getZoom()); });
    return container;
  },
});
new CenterControl().addTo(map);

function makeIcon(v) {
  const color = vesselColor(v);
  const rot = v.heading ?? v.course ?? 0;
  const html = `<div class="vessel-symbol" style="transform:rotate(${rot}deg);color:${color}"><svg viewBox="0 0 48 48" width="30" height="30"><path d="M24 3 37 41 24 34 11 41Z" fill="currentColor" stroke="rgba(0,0,0,.55)" stroke-width="2"/><path d="M24 11 28 31 24 29 20 31Z" fill="rgba(255,255,255,.28)"/></svg></div>`;
  return L.divIcon({ className: 'vessel-div-icon', html, iconSize: [30, 30], iconAnchor: [15, 15] });
}
function makeListIcon(v) {
  const color = vesselColor(v);
  return `<svg viewBox="0 0 48 48" width="18" height="18"><path d="M24 3 37 41 24 34 11 41Z" fill="${color}" stroke="rgba(0,0,0,.45)" stroke-width="3"/></svg>`;
}
function updateMarker(v) {
  if (v.lat == null || v.lon == null) { removeMarker(v.mmsi); return; }
  const ll = [v.lat, v.lon];
  if (markers[v.mmsi]) markers[v.mmsi].setLatLng(ll).setIcon(makeIcon(v));
  else markers[v.mmsi] = L.marker(ll, { icon: makeIcon(v) }).on('click', () => selectVessel(v.mmsi)).addTo(map);
}
function removeMarker(mmsi) { if (markers[mmsi]) { map.removeLayer(markers[mmsi]); delete markers[mmsi]; } }

function updateTrail(v) {
  if (!showTrails || !v.track_points || v.track_points.length < 2) return;
  const latlngs = v.track_points.map(p => [p[0], p[1]]);
  const color = vesselColor(v);
  if (trails[v.mmsi]) trails[v.mmsi].setLatLngs(latlngs).setStyle({ color, dashArray: v.gone ? '4 6' : null, opacity: v.gone ? 0.35 : 0.65 });
  else trails[v.mmsi] = L.polyline(latlngs, { color, weight: 1.8, opacity: v.gone ? 0.35 : 0.65, dashArray: v.gone ? '4 6' : null }).addTo(map);
}
function removeTrail(mmsi) { if (trails[mmsi]) { map.removeLayer(trails[mmsi]); delete trails[mmsi]; } }
function toggleTrails() {
  showTrails = el('trails-toggle').checked;
  if (!showTrails) Object.keys(trails).forEach(removeTrail);
  else [...Object.values(vesselsData), ...Object.values(historyData)].forEach(updateTrail);
}

const RING_ALL = [5, 10, 25, 50, 75, 100, 150, 200];
const RING_DEFAULT = [10, 25, 50];
function getRingNm() {
  try { const saved = JSON.parse(localStorage.getItem(RING_LS_KEY)); if (Array.isArray(saved) && saved.length) return saved; } catch(e) {}
  return RING_DEFAULT.slice();
}
function toggleRingNm(nm) {
  const cur = getRingNm();
  const next = cur.includes(nm) ? cur.filter(v => v !== nm) : [...cur, nm].sort((a, b) => a - b);
  try { localStorage.setItem(RING_LS_KEY, JSON.stringify(next)); } catch(e) {}
  renderRingOpts(); drawRings();
}
function renderRingOpts() {
  const c = el('rings-opts'); if (!c) return;
  const active = getRingNm();
  c.innerHTML = RING_ALL.map(nm => `<button class="ring-chip${active.includes(nm) ? ' active' : ''}" onclick="toggleRingNm(${nm})">${nm}nm</button>`).join('');
}
function drawRings() {
  rangeRings.forEach(r => map.removeLayer(r)); rangeRings = [];
  if (!showRings || !receiverPos) return;
  getRingNm().forEach(nm => {
    rangeRings.push(L.circle([receiverPos.lat, receiverPos.lon], { radius: nm * 1852, color: '#2a3550', weight: 1, fill: false, dashArray: '3 9' }).addTo(map));
  });
}
function toggleRings() { showRings = el('rings-toggle').checked; drawRings(); }
function updateReceiverMarker(pos) {
  if (!pos || pos.lat == null || pos.lon == null) return;
  const ll = [pos.lat, pos.lon];
  if (receiverMarker) { receiverMarker.setLatLng(ll); return; }
  receiverMarker = L.circleMarker(ll, { radius: 5, color: '#e8b04f', fillColor: '#e8b04f', fillOpacity: 1, weight: 2 }).bindTooltip('CD receiver').addTo(map);
}

function switchTab(tab) { currentTab = tab; el('tab-active').classList.toggle('active', tab === 'active'); el('tab-history').classList.toggle('active', tab === 'history'); renderList(); }
function selectVessel(mmsi) { selectedMmsi = selectedMmsi === mmsi ? null : mmsi; followMmsi = null; renderList(); const row = el('row-' + mmsi); if (row) row.scrollIntoView({ block: 'nearest' }); }
function buildExpanded(v) {
  const followActive = followMmsi === v.mmsi;
  const rows = [
    ['MMSI', v.mmsi], ['Kind', v.mmsi_kind || '—'], ['Country', v.country || '—'], ['Flag', v.country_flag || '—'], ['MID', v.mid || '—'],
    ['IMO', v.imo || '—'], ['Callsign', v.callsign || '—'], ['Type', v.ship_type_text || '—'], ['Status', v.nav_status_text || '—'],
    ['Speed', fmtSpd(v.speed)], ['Course', fmtCourse(v.course)], ['Heading', fmtCourse(v.heading)], ['Distance', fmtDist(v.distance)],
    ['CPA', fmtDist(v.cpa_distance_nm)], ['TCPA', fmtTcpa(v.tcpa_min)], ['Approaching', v.approaching ? 'Yes' : 'No'], ['Watchlist', isWatched(v.mmsi) ? 'Yes' : 'No'],
    ['Length', fmtMeters(v.length_m)], ['Beam', fmtMeters(v.beam_m)], ['Destination', v.destination || '—'], ['Messages', v.messages ?? '—'],
    ['First seen', fmtAge(v.first_seen)], ['Last seen', fmtAge(v.last_seen)],
  ];
  return `<div class="ac-expanded">
    <div class="exp-type">${esc(vesselLabel(v))}</div>
    <div class="exp-grid">${rows.map(([l, val]) => `<div class="exp-cell"><div class="exp-label">${l}</div><div class="exp-value">${esc(val)}</div></div>`).join('')}</div>
    <div class="exp-btns"><button class="exp-btn" onclick="centerOnSelected()">⊕ Center</button><button class="exp-btn${followActive ? ' active' : ''}" id="follow-btn" onclick="toggleFollow()">${followActive ? '⏸ Following' : '▶ Follow'}</button></div>
    <div class="exp-btns" style="margin-top:8px"><button class="exp-btn${isWatched(v.mmsi) ? ' active' : ''}" onclick="toggleWatchSelected()">${isWatched(v.mmsi) ? '★ Watched' : '☆ Watch'}</button><button class="exp-btn" onclick="exportSelected('gpx')">GPX</button><button class="exp-btn" onclick="exportSelected('geojson')">GeoJSON</button></div>
  </div>`;
}
function centerOnSelected() { const v = vesselsData[selectedMmsi] || historyData[selectedMmsi]; if (v?.lat != null) map.setView([v.lat, v.lon], Math.max(map.getZoom(), 11)); }
function toggleFollow() { followMmsi = followMmsi === selectedMmsi ? null : selectedMmsi; if (followMmsi) centerOnSelected(); updateFollowBtn(); }
function toggleWatchSelected() {
  if (!selectedMmsi) return;
  if (watchlist.has(selectedMmsi)) watchlist.delete(selectedMmsi); else watchlist.add(selectedMmsi);
  saveWatchlist();
  renderList();
}
function exportSelected(kind) {
  if (!selectedMmsi) return;
  window.location.href = `/api/vessels/${selectedMmsi}/${kind}`;
}
function setAlertRadius(value) {
  const next = parseFloat(value);
  if (!Number.isFinite(next) || next < 0) return;
  alertRadiusNm = next;
  localStorage.setItem('ais_alert_radius_nm', String(next));
  const msg = el('alert-status-msg');
  if (msg) msg.textContent = `Alerting for approaching vessels with CPA ≤ ${next} nm`;
  renderList();
}
function loadAlertSettings() {
  const input = el('alert-radius-input');
  if (input) input.value = String(alertRadiusNm);
  const msg = el('alert-status-msg');
  if (msg) msg.textContent = `Alerting for approaching vessels with CPA ≤ ${alertRadiusNm} nm`;
}
function updateFollowBtn() { const btn = el('follow-btn'); if (!btn) return; const active = !!(followMmsi && followMmsi === selectedMmsi); btn.classList.toggle('active', active); btn.textContent = active ? '⏸ Following' : '▶ Follow'; }
function renderList() {
  const list = el('vessel-list'); const prevScroll = list.scrollTop;
  const data = currentTab === 'active' ? Object.values(vesselsData) : Object.values(historyData);
  el('badge-active').textContent = Object.keys(vesselsData).length;
  el('badge-history').textContent = Object.keys(historyData).length;
  if (!data.length) { list.innerHTML = `<div class="list-empty">${currentTab === 'active' ? 'No vessels detected' : 'No history this session'}</div>`; return; }
  const sorted = data.slice().sort((a, b) => currentTab === 'history' ? (b.gone_at || 0) - (a.gone_at || 0) : (a.distance ?? 99999) - (b.distance ?? 99999));
  list.innerHTML = sorted.map(v => {
    const label = esc(vesselLabel(v));
    const cpa = v.cpa_distance_nm != null ? `CPA ${v.cpa_distance_nm}nm${v.tcpa_min ? ' / ' + v.tcpa_min + 'm' : ''}` : null;
    const sub = esc([isWatched(v.mmsi) ? '★' : null, vesselCountry(v) || null, v.ship_type_text || null, cpa, v.callsign || null, v.nav_status_text || null].filter(Boolean).join(' · ') || '—');
    const sel = selectedMmsi === v.mmsi ? ' selected' : ''; const gone = v.gone ? ' gone' : ''; const watched = isWatched(v.mmsi) ? ' watched' : ''; const alert = isAlert(v) ? ' cpa-alert' : '';
    const row = `<div class="ac-row${sel}${gone}${watched}${alert}" id="row-${v.mmsi}" onclick="selectVessel('${v.mmsi}')"><div class="ac-icon-wrap">${makeListIcon(v)}</div><div class="ac-info"><div class="ac-callsign">${label}</div><div class="ac-sub">${sub}</div></div><div class="ac-right"><div class="ac-alt" style="color:${vesselColor(v)}">${fmtSpd(v.speed)}</div><div class="ac-dist">${fmtDist(v.distance)}</div></div></div>`;
    return row + (selectedMmsi === v.mmsi ? buildExpanded(v) : '');
  }).join('');
  list.scrollTop = prevScroll;
}

async function poll() {
  try {
    const d = await (await fetch('/api/vessels')).json();
    const effRecv = d.effective_receiver?.lat != null ? d.effective_receiver : d.receiver?.lat != null ? d.receiver : null;
    if (effRecv) { receiverPos = effRecv; updateReceiverMarker(receiverPos); drawRings(); }
    el('ais-dot').className = 'status-dot ' + (d.ais_running ? 'on' : 'off');
    const startBtn = el('ais-start-btn');
    startBtn.classList.toggle('active', d.ais_running); startBtn.textContent = d.ais_running ? 'Running…' : 'Start';
    el('stat-count').textContent = `${d.stats.active_count} vessels`;
    const dbLabel = el('db-version-label');
    if (dbLabel && d.stats.known_vessels != null) dbLabel.textContent = `${d.stats.known_vessels} known vessels`;
    const parts = [];
    if (d.stats.closest) parts.push(`closest: ${d.stats.closest.name} ${d.stats.closest.distance} nm`);
    if (d.stats.farthest) parts.push(`farthest: ${d.stats.farthest.name} ${d.stats.farthest.distance} nm`);
    if (d.stats.fastest) parts.push(`fastest: ${d.stats.fastest.name} ${d.stats.fastest.speed} kt`);
    if (d.stats.messages_per_min != null) parts.push(`${d.stats.messages_per_min} msg/min`);
    el('stat-range').textContent = parts.join(' | ');
    const input = el('ais-input-label'); if (input) input.textContent = `AIS-catcher TCP :${d.decoder.json_port || 10111} · UDP :${d.udp.port}`;
    const msg = el('ais-status-msg');
    if (msg) {
      msg.textContent = d.decoder.error || (d.udp.running ? `Listening for AIS-catcher JSON on TCP ${d.decoder.json_port || 10111} and NMEA on UDP ${d.udp.port}. Signal: ${d.stats.messages_per_min ?? 0} msg/min. Offline maps are loaded from OPS-TOC mbtileserver.` : d.udp.error || 'AIS UDP listener offline');
      msg.style.color = d.decoder.error || d.udp.error ? 'var(--red)' : 'var(--muted)';
    }
    const newActive = {}; d.active.forEach(v => { newActive[v.mmsi] = v; });
    Object.keys(vesselsData).forEach(mmsi => { if (!newActive[mmsi]) removeMarker(mmsi); });
    vesselsData = newActive;
    Object.values(vesselsData).forEach(v => { updateMarker(v); updateTrail(v); });
    const newHistory = {}; d.history.forEach(v => { newHistory[v.mmsi] = v; }); historyData = newHistory;
    if (followMmsi) { const v = vesselsData[followMmsi]; if (v?.lat != null) map.panTo([v.lat, v.lon], { animate: true, duration: 0.8 }); }
    if (selectedMmsi && !vesselsData[selectedMmsi] && !historyData[selectedMmsi]) { selectedMmsi = null; followMmsi = null; }
    renderList();
  } catch(e) {}
  setTimeout(poll, 2000);
}

async function aisAction(action) { try { await fetch(`/api/ais/${action}`, { method: 'POST' }); setTimeout(poll, 500); } catch(e) {} }
function toggleSettings() { const s = el('settings'); s.classList.toggle('hidden'); if (!s.classList.contains('hidden')) { renderLayerPicker(); renderRingOpts(); loadReceiverStatus(); loadVersion(); loadVesselDbStatus(); loadAlertSettings(); } }
function setAccent(color) { document.documentElement.style.setProperty('--accent', color); document.documentElement.style.setProperty('--accent-dim', color + '2e'); localStorage.setItem('ais_accent', color); }
function loadAccent() { const c = localStorage.getItem('ais_accent'); if (c) { setAccent(c); const input = el('accent-input'); if (input) input.value = c; } }

async function loadReceiverStatus() {
  try {
    const d = await (await fetch('/api/receiver')).json();
    const statusEl = el('pos-status');
    if (statusEl) {
      if (d.lat != null && d.lon != null) { const fix = d.gps_status?.fix != null ? ` · fix ${d.gps_status.fix}` : ''; const sats = d.gps_status?.sats != null ? ` · ${d.gps_status.sats} sats` : ''; statusEl.textContent = `${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}${fix}${sats}`; statusEl.style.color = 'var(--green)'; }
      else { statusEl.textContent = d.source === 'auto' ? 'No OPS-TOC GPS fix yet' : 'No GPS fix'; statusEl.style.color = 'var(--muted)'; }
    }
    ['auto','opstoc','om','manual'].forEach(s => { const btn = el('pos-' + s); if (btn) btn.classList.toggle('active', d.source === s); });
    const manEl = el('pos-manual-inputs');
    if (manEl) { manEl.style.display = d.source === 'manual' ? 'flex' : 'none'; if (d.source === 'manual' && d.lat != null && d.lon != null) { el('pos-lat').value ||= d.lat.toFixed(6); el('pos-lon').value ||= d.lon.toFixed(6); } }
  } catch(e) {}
}
async function setGpsSource(source) { await fetch('/api/receiver', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ source }) }); await loadReceiverStatus(); }
let _pickingLocation = false;
function startLocationPick() { _pickingLocation = true; el('pick-hint').style.display = ''; map.getContainer().style.cursor = 'crosshair'; }
function cancelLocationPick() { _pickingLocation = false; el('pick-hint').style.display = 'none'; map.getContainer().style.cursor = ''; }
map.on('click', async e => { if (!_pickingLocation) return; el('pos-lat').value = e.latlng.lat.toFixed(6); el('pos-lon').value = e.latlng.lng.toFixed(6); cancelLocationPick(); await applyManualPos(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && _pickingLocation) cancelLocationPick(); });
async function applyManualPos() { const lat = parseFloat(el('pos-lat').value); const lon = parseFloat(el('pos-lon').value); if (!Number.isFinite(lat) || !Number.isFinite(lon)) return; await fetch('/api/receiver', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ source: 'manual', lat, lon }) }); receiverPos = { lat, lon }; updateReceiverMarker(receiverPos); drawRings(); map.setView([lat, lon], map.getZoom()); await loadReceiverStatus(); }


async function loadVesselDbStatus() {
  try {
    const d = await (await fetch('/api/db/status')).json();
    const label = el('db-version-label');
    const msg = el('db-status-msg');
    if (label) label.textContent = `${d.vessel_count || 0} known vessels`;
    if (msg) {
      const updated = d.updated ? ` · ${d.updated}` : '';
      msg.textContent = `${d.mid_count || 0} MID country prefixes${updated}`;
      msg.style.color = d.error ? 'var(--red)' : 'var(--muted)';
      if (d.error) msg.textContent = d.error;
    }
  } catch(e) {}
}

async function updateVesselDb() {
  const msg = el('db-status-msg');
  if (msg) msg.textContent = 'Updating from received AIS messages...';
  try {
    const d = await (await fetch('/api/db/update', { method: 'POST' })).json();
    if (msg) msg.textContent = d.ok ? `Updated ${d.updated} entries · ${d.vessel_count} known vessels` : (d.error || 'Update failed');
    await loadVesselDbStatus();
  } catch(e) { if (msg) msg.textContent = 'Update failed'; }
}

function exportVesselDb() {
  window.location.href = '/api/db/export';
}

function pickVesselDbImport() {
  const input = el('db-import-input');
  if (input) input.click();
}

async function importVesselDbFile(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const msg = el('db-status-msg');
  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    const d = await (await fetch('/api/db/import', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
    })).json();
    if (msg) msg.textContent = d.ok ? `Imported ${d.imported} entries · ${d.vessel_count} known vessels` : (d.error || 'Import failed');
    await loadVesselDbStatus();
  } catch(e) { if (msg) msg.textContent = 'Import failed'; }
  input.value = '';
}

async function clearVesselDb() {
  if (!confirm('Clear the local AIS vessel DB?')) return;
  const msg = el('db-status-msg');
  try {
    const d = await (await fetch('/api/db/clear', { method: 'POST' })).json();
    if (msg) msg.textContent = d.ok ? 'Local AIS vessel DB cleared' : (d.error || 'Clear failed');
    await loadVesselDbStatus();
  } catch(e) { if (msg) msg.textContent = 'Clear failed'; }
}

async function loadVersion() { try { const d = await (await fetch('/api/version')).json(); el('app-version').textContent = `AIS App ${d.version} · ${d.commit}`; } catch(e) {} }
async function checkUpdate() { const msg = el('update-msg'); msg.textContent = 'Checking…'; try { const d = await (await fetch('/api/system/check-update', { method: 'POST' })).json(); msg.textContent = d.ok ? (d.behind ? `${d.behind} commits behind` : 'Up to date') : d.error; } catch(e) { msg.textContent = 'Update check failed'; } }
async function updateApp() { const msg = el('update-msg'); msg.textContent = 'Updating…'; try { const d = await (await fetch('/api/system/update', { method: 'POST' })).json(); msg.textContent = d.ok ? 'Updated, restarting…' : d.error; } catch(e) { msg.textContent = 'Update failed'; } }
async function appRestart() { await fetch('/api/system/restart', { method: 'POST' }); }
async function appShutdown() { await fetch('/api/system/shutdown', { method: 'POST' }); }
function tickClock() { const d = new Date(); el('hdr-clock').textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }

loadAccent(); renderRingOpts(); poll(); loadReceiverStatus(); loadVesselDbStatus(); loadAlertSettings(); loadVersion(); tickClock(); setInterval(tickClock, 1000); setInterval(loadReceiverStatus, 10000);

// =================================================================
// UX Flow Capturer — Popup
//
// Two views (no splash):
//   - SETUP   (default; or "edit" when launched from State 3)
//   - ACTIVE  (shown immediately when a flow is recording)
//
// Required fields (Flow Name, Client, Platform) gate "Begin Capture".
// Active view polls /api/session/<id> for live state/screen counts.
// =================================================================

const SERVER = 'http://127.0.0.1:8000';
const CLIENTS_KEY = 'ux-known-clients';
const REQUIRED_FIELDS = ['flow_name', 'client', 'platform'];

// Sections (states)
const stateSetup = document.getElementById('state-setup');
const stateActive = document.getElementById('state-active');
const banner = document.getElementById('server-banner');

// Setup
const setupTitle = document.getElementById('setup-title');
const setupSubmit = document.getElementById('setup-submit');
const setupCancel = document.getElementById('setup-cancel');
const setupBack = document.getElementById('setup-back');
const flowForm = document.getElementById('flow-form');
const clientList = document.getElementById('client-list');

// Active
const activeFlowName = document.getElementById('active-flow-name');
const saveBtn = document.getElementById('save-btn');
const editDetailsBtn = document.getElementById('edit-details-btn');
const counterEl = document.getElementById('counter');

// In-popup state
let currentSessionId = null;
let isEditingDuringRecording = false;
let counterTimer = null;

// ----- Boot -----
document.addEventListener('DOMContentLoaded', async () => {
  refreshClientDatalist();
  checkServerHealth();
  await routeBasedOnRecordingState();

  setupCancel.addEventListener('click', onSetupCancel);
  setupBack.addEventListener('click', onSetupCancel);
  flowForm.addEventListener('submit', onFormSubmit);

  // Validate required fields on every change so the submit button reflects state.
  flowForm.addEventListener('input', updateSubmitState);
  flowForm.addEventListener('change', updateSubmitState);

  saveBtn.addEventListener('click', onSave);
  editDetailsBtn.addEventListener('click', () => showSetup({ editing: true }));
});

// ----- Server health -----
async function checkServerHealth() {
  try {
    const res = await fetch(`${SERVER}/health`, { cache: 'no-store' });
    if (res.ok) banner.classList.add('hidden');
    else throw new Error('non-ok');
  } catch (e) {
    banner.classList.remove('hidden');
  }
}

// ----- View routing -----
function showOnly(section) {
  for (const el of [stateSetup, stateActive]) {
    el.classList.toggle('hidden', el !== section);
  }
  if (section !== stateActive) stopCounterPolling();
}

async function routeBasedOnRecordingState() {
  const res = await new Promise((resolve) =>
    chrome.storage.local.get(['isRecording', 'sessionId', 'flowMetadata'], resolve)
  );
  if (res.isRecording && res.sessionId) {
    currentSessionId = res.sessionId;
    const meta = res.flowMetadata || {};
    activeFlowName.textContent = meta.flow_name || res.sessionId;
    showOnly(stateActive);
    startCounterPolling();
  } else {
    // Default: open straight into the New Flow form (no splash).
    isEditingDuringRecording = false;
    flowForm.reset();
    setupTitle.textContent = 'New Flow';
    setupSubmit.textContent = 'Begin Capture';
    setupCancel.classList.add('hidden');
    setupBack.classList.add('hidden');
    showOnly(stateSetup);
    updateSubmitState();
  }
}

// ----- Setup view -----
function showSetup({ editing }) {
  isEditingDuringRecording = !!editing;
  setupTitle.textContent = editing ? 'Edit Flow' : 'New Flow';
  setupSubmit.textContent = editing ? 'Save' : 'Begin Capture';
  setupCancel.classList.toggle('hidden', !editing);
  setupBack.classList.toggle('hidden', !editing);

  flowForm.reset();
  if (editing) {
    chrome.storage.local.get(['flowMetadata'], (res) => {
      const meta = res.flowMetadata || {};
      flowForm.flow_name.value = meta.flow_name || '';
      flowForm.client.value = meta.client || meta.sector || '';
      flowForm.platform.value = meta.platform || '';
      flowForm.target_user.value = meta.target_user || '';
      flowForm.user_goal.value = meta.user_goal || '';
      updateSubmitState();
    });
  }
  showOnly(stateSetup);
  updateSubmitState();
}

function onSetupCancel() {
  if (isEditingDuringRecording) {
    showOnly(stateActive);
    startCounterPolling();
  }
  // (When NOT editing during recording, there's nowhere to go back to —
  // setup is the entry view. The cancel button stays hidden in that case.)
}

function updateSubmitState() {
  const filled = REQUIRED_FIELDS.every((name) => {
    const el = flowForm.elements[name];
    return el && (el.value || '').trim().length > 0;
  });
  setupSubmit.disabled = !filled;
}

async function onFormSubmit(e) {
  e.preventDefault();
  const fd = new FormData(flowForm);
  const meta = Object.fromEntries(
    Array.from(fd.entries()).map(([k, v]) => [k, (v || '').trim()])
  );
  for (const name of REQUIRED_FIELDS) {
    if (!meta[name]) return; // belt + braces — submit button should already be disabled
  }

  rememberClient(meta.client);

  // Editing while recording — just save metadata, stay in active view.
  if (isEditingDuringRecording && currentSessionId) {
    await fetch(`${SERVER}/api/session/${encodeURIComponent(currentSessionId)}/flow_metadata`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(meta),
    }).catch(() => {});
    chrome.storage.local.set({ flowMetadata: meta });
    activeFlowName.textContent = meta.flow_name;
    showOnly(stateActive);
    startCounterPolling();
    return;
  }

  // New flow — start capture.
  const sessionId = slugify(meta.flow_name);
  await fetch(`${SERVER}/api/session/${encodeURIComponent(sessionId)}/flow_metadata`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(meta),
  }).catch(() => {});

  chrome.storage.local.set(
    { isRecording: true, sessionId, flowMetadata: meta },
    () => {
      chrome.runtime.sendMessage({ action: 'startRecording', sessionId, flowMetadata: meta });
      window.close();
    }
  );
}

// ----- Active state -----
function onSave() {
  chrome.storage.local.set({ isRecording: false }, () => {
    chrome.runtime.sendMessage({ action: 'stopRecording' });
    currentSessionId = null;
    stopCounterPolling();
    routeBasedOnRecordingState();
  });
}

// Live counter while the popup is open.
function startCounterPolling() {
  stopCounterPolling();
  refreshCounter();
  counterTimer = setInterval(refreshCounter, 1500);
}
function stopCounterPolling() {
  if (counterTimer) { clearInterval(counterTimer); counterTimer = null; }
}
async function refreshCounter() {
  if (!currentSessionId) return;
  try {
    const res = await fetch(`${SERVER}/api/session/${encodeURIComponent(currentSessionId)}`);
    if (!res.ok) return;
    const data = await res.json();
    const states = Object.keys(data.states || {});
    const screens = states.reduce(
      (acc, k) => acc + ((data.states[k] || {}).screenshots || []).length, 0
    );
    counterEl.textContent =
      `${states.length} state${states.length === 1 ? '' : 's'} · ${screens} screen${screens === 1 ? '' : 's'}`;
  } catch (_) {}
}

// ----- Client autocomplete (free-text dropdown) -----
function rememberClient(name) {
  const v = (name || '').trim();
  if (!v) return;
  try {
    const list = JSON.parse(localStorage.getItem(CLIENTS_KEY) || '[]');
    if (!list.includes(v)) {
      list.push(v);
      localStorage.setItem(CLIENTS_KEY, JSON.stringify(list));
    }
  } catch (_) {}
}

function refreshClientDatalist() {
  let list = [];
  try { list = JSON.parse(localStorage.getItem(CLIENTS_KEY) || '[]'); } catch (_) {}
  clientList.innerHTML = '';
  for (const c of list) {
    const opt = document.createElement('option');
    opt.value = c;
    clientList.appendChild(opt);
  }
}

// ----- Helpers -----
function slugify(s) {
  return (s || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .substring(0, 60) || `session-${Date.now()}`;
}

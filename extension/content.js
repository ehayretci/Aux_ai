// ============================================================
// UX Flow Capturer — Content Script
// Injected into every page. Handles:
//   1) Floating Action Button (FAB) — opens a small menu (Whole / Cropped)
//   2) Interactive element click detection (mousedown)
//   3) DOM mutation observation for SPA transitions
//   4) Snipping-tool region-select overlay (cropped capture)
// ============================================================

let fab = null;
let fabMenu = null;
let mutationObserver = null;
let settleTimer = null;
let isRecordingCached = false;
let isDragging = false;
let dragStartX = 0, dragStartY = 0;
let fabStartX = 0, fabStartY = 0;
let hasDragged = false;

// Region-select state
let regionRoot = null;
let regionSelStart = null;
let regionSelRect = null;

console.log('[UX CS] Content script loaded on:', window.location.href);

// ----- FAB Creation -----
function createFab() {
  if (fab) return;
  console.log('[UX CS] Creating FAB');

  fab = document.createElement('div');
  fab.id = 'ux-capturer-fab';
  fab.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
      <circle cx="12" cy="13" r="4"/>
    </svg>
  `;

  // Floating menu (hidden until FAB is clicked)
  fabMenu = document.createElement('div');
  fabMenu.id = 'ux-capturer-menu';
  fabMenu.innerHTML = `
    <button class="ux-menu-item" data-mode="whole">
      <span class="ux-menu-ico">▭</span>
      <span class="ux-menu-text">Whole Screen</span>
    </button>
    <button class="ux-menu-item" data-mode="cropped">
      <span class="ux-menu-ico">⬚</span>
      <span class="ux-menu-text">Cropped Screen</span>
    </button>
  `;
  fabMenu.style.display = 'none';

  const style = document.createElement('style');
  style.id = 'ux-capturer-fab-style';
  style.textContent = `
    #ux-capturer-fab {
      position: fixed;
      bottom: 24px;
      right: 24px;
      width: 56px;
      height: 56px;
      background: linear-gradient(135deg, #ff3b30, #c0392b);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: grab;
      z-index: 2147483647;
      box-shadow: 0 4px 16px rgba(255, 59, 48, 0.5);
      transition: box-shadow 0.15s ease, transform 0.15s ease;
      user-select: none;
      touch-action: none;
    }
    #ux-capturer-fab:hover { box-shadow: 0 6px 24px rgba(255, 59, 48, 0.6); }
    #ux-capturer-fab.ux-dragging { cursor: grabbing; box-shadow: 0 8px 32px rgba(255, 59, 48, 0.7); }
    #ux-capturer-fab.ux-flash { background: #34c759 !important; box-shadow: 0 4px 16px rgba(52, 199, 89, 0.5) !important; }
    #ux-capturer-fab.ux-active { transform: rotate(45deg); }

    #ux-capturer-menu {
      position: fixed;
      z-index: 2147483647;
      background: #14161a;
      border: 1px solid #2a2d34;
      border-radius: 10px;
      padding: 6px;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6);
      display: flex;
      flex-direction: column;
      min-width: 180px;
      font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    }
    #ux-capturer-menu .ux-menu-item {
      background: transparent;
      border: none;
      color: #e6e7ea;
      padding: 9px 12px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 500;
      text-align: left;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 10px;
      font-family: inherit;
    }
    #ux-capturer-menu .ux-menu-item:hover { background: #1d1f24; }
    #ux-capturer-menu .ux-menu-ico {
      width: 22px;
      text-align: center;
      color: #ff3b30;
      font-weight: 700;
    }
  `;

  document.documentElement.appendChild(style);
  document.documentElement.appendChild(fab);
  document.documentElement.appendChild(fabMenu);

  // Drag-or-click on the FAB
  fab.addEventListener('mousedown', (e) => {
    e.stopPropagation(); e.stopImmediatePropagation(); e.preventDefault();
    isDragging = true; hasDragged = false;
    dragStartX = e.clientX; dragStartY = e.clientY;
    const rect = fab.getBoundingClientRect();
    fabStartX = rect.left; fabStartY = rect.top;
    fab.classList.add('ux-dragging');
  }, true);

  document.addEventListener('mousemove', (e) => {
    if (!isDragging || !fab) return;
    const dx = e.clientX - dragStartX;
    const dy = e.clientY - dragStartY;
    if (Math.abs(dx) > 5 || Math.abs(dy) > 5) hasDragged = true;
    let newX = Math.max(0, Math.min(fabStartX + dx, window.innerWidth - 56));
    let newY = Math.max(0, Math.min(fabStartY + dy, window.innerHeight - 56));
    fab.style.left = newX + 'px';
    fab.style.top = newY + 'px';
    fab.style.right = 'auto';
    fab.style.bottom = 'auto';
  });

  document.addEventListener('mouseup', () => {
    if (!isDragging) return;
    isDragging = false;
    if (fab) fab.classList.remove('ux-dragging');
    if (!hasDragged) toggleFabMenu();
  });

  // Menu item handlers
  fabMenu.addEventListener('click', (e) => {
    const btn = e.target.closest('.ux-menu-item');
    if (!btn) return;
    const mode = btn.dataset.mode;
    hideFabMenu();
    if (mode === 'whole') {
      flashFab();
      try { chrome.runtime.sendMessage({ action: 'fabCaptureWhole' }); } catch (_) {}
    } else if (mode === 'cropped') {
      try { chrome.runtime.sendMessage({ action: 'fabCaptureCropped' }); } catch (_) {}
    }
  });

  // Click anywhere else closes the menu
  document.addEventListener('mousedown', (e) => {
    if (!fabMenu || fabMenu.style.display === 'none') return;
    if (e.target.closest('#ux-capturer-menu')) return;
    if (e.target.closest('#ux-capturer-fab')) return;
    hideFabMenu();
  });

  console.log('[UX CS] FAB created successfully');
}

function flashFab() {
  if (!fab) return;
  fab.classList.add('ux-flash');
  setTimeout(() => { if (fab) fab.classList.remove('ux-flash'); }, 300);
}

function toggleFabMenu() {
  if (!fabMenu) return;
  if (fabMenu.style.display === 'none') showFabMenu();
  else hideFabMenu();
}

function showFabMenu() {
  if (!fab || !fabMenu) return;
  const r = fab.getBoundingClientRect();
  // Position the menu just to the left and slightly above the FAB.
  const menuW = 200;
  const menuH = 96;
  let left = r.left - menuW - 12;
  let top = r.top + (r.height / 2) - (menuH / 2);
  if (left < 8) left = r.right + 12;
  top = Math.max(8, Math.min(window.innerHeight - menuH - 8, top));
  fabMenu.style.left = left + 'px';
  fabMenu.style.top = top + 'px';
  fabMenu.style.display = 'flex';
  fab.classList.add('ux-active');
}

function hideFabMenu() {
  if (fabMenu) fabMenu.style.display = 'none';
  if (fab) fab.classList.remove('ux-active');
}

function removeFab() {
  hideFabMenu();
  if (fab) { fab.remove(); fab = null; }
  if (fabMenu) { fabMenu.remove(); fabMenu = null; }
  const style = document.getElementById('ux-capturer-fab-style');
  if (style) style.remove();
  isRecordingCached = false;
}

function hideFab() {
  if (fab) fab.style.display = 'none';
  hideFabMenu();
}
function showFab() { if (fab) fab.style.display = 'flex'; }

// ----- Region select (snipping tool) -----
function startRegionSelect() {
  if (regionRoot) return;
  console.log('[UX CS] Starting region select');
  hideFab();

  regionRoot = document.createElement('div');
  regionRoot.id = 'ux-region-overlay';
  regionRoot.innerHTML = `
    <div class="ux-region-mask"></div>
    <div class="ux-region-rect"></div>
    <div class="ux-region-hint">Drag to select a region · Esc to cancel</div>
  `;

  const style = document.createElement('style');
  style.id = 'ux-region-style';
  style.textContent = `
    #ux-region-overlay {
      position: fixed; inset: 0;
      z-index: 2147483646;
      cursor: crosshair;
      user-select: none;
    }
    #ux-region-overlay .ux-region-mask {
      position: absolute; inset: 0;
      background: rgba(0,0,0,0.40);
      pointer-events: none;
    }
    #ux-region-overlay .ux-region-rect {
      position: absolute;
      border: 2px solid #ff3b30;
      background: rgba(255, 59, 48, 0.06);
      box-shadow: 0 0 0 99999px rgba(0,0,0,0.40);
      pointer-events: none;
      display: none;
    }
    #ux-region-overlay .ux-region-hint {
      position: absolute;
      top: 16px; left: 50%;
      transform: translateX(-50%);
      background: #14161a;
      color: #e6e7ea;
      padding: 8px 14px;
      border-radius: 999px;
      font: 600 12px/1 -apple-system, sans-serif;
      letter-spacing: 0.04em;
      pointer-events: none;
      box-shadow: 0 6px 20px rgba(0,0,0,0.6);
    }
  `;
  document.documentElement.appendChild(style);
  document.documentElement.appendChild(regionRoot);

  regionSelStart = null;
  regionSelRect = null;

  regionRoot.addEventListener('mousedown', onRegionDown);
  regionRoot.addEventListener('mousemove', onRegionMove);
  regionRoot.addEventListener('mouseup', onRegionUp);
  document.addEventListener('keydown', onRegionKey, true);
}

function endRegionSelect() {
  if (regionRoot) regionRoot.remove();
  const s = document.getElementById('ux-region-style');
  if (s) s.remove();
  regionRoot = null;
  regionSelStart = null;
  regionSelRect = null;
  document.removeEventListener('keydown', onRegionKey, true);
  showFab();
}

function onRegionDown(e) {
  e.preventDefault(); e.stopPropagation();
  regionSelStart = { x: e.clientX, y: e.clientY };
  const rect = regionRoot.querySelector('.ux-region-rect');
  rect.style.display = 'block';
  rect.style.left = e.clientX + 'px';
  rect.style.top = e.clientY + 'px';
  rect.style.width = '0px';
  rect.style.height = '0px';
}

function onRegionMove(e) {
  if (!regionSelStart) return;
  const x = Math.min(e.clientX, regionSelStart.x);
  const y = Math.min(e.clientY, regionSelStart.y);
  const w = Math.abs(e.clientX - regionSelStart.x);
  const h = Math.abs(e.clientY - regionSelStart.y);
  const rect = regionRoot.querySelector('.ux-region-rect');
  rect.style.left = x + 'px';
  rect.style.top = y + 'px';
  rect.style.width = w + 'px';
  rect.style.height = h + 'px';
  regionSelRect = { x, y, w, h };
}

function onRegionUp(e) {
  if (!regionSelStart || !regionSelRect) { endRegionSelect(); return; }
  const r = regionSelRect;
  endRegionSelect();
  if (r.w < 8 || r.h < 8) {
    console.log('[UX CS] Region too small — cancelled');
    return;
  }
  try {
    chrome.runtime.sendMessage({
      action: 'regionSelected',
      rect: r,
      dpr: window.devicePixelRatio || 1,
    });
  } catch (err) {
    console.error('[UX CS] regionSelected send failed:', err);
  }
}

function onRegionKey(e) {
  if (e.key === 'Escape') {
    e.preventDefault();
    endRegionSelect();
  }
}

// ----- Interactive Element Detection -----
const INTERACTIVE_TAGS = new Set(['A', 'BUTTON', 'SELECT', 'SUMMARY']);
const INTERACTIVE_INPUT_TYPES = new Set(['submit', 'button', 'reset']);
const INTERACTIVE_ROLES = new Set(['button', 'link', 'tab', 'menuitem', 'option', 'checkbox', 'radio', 'switch']);

function isInteractiveElement(el) {
  let current = el;
  let depth = 0;
  while (current && current !== document.body && depth < 6) {
    if (INTERACTIVE_TAGS.has(current.tagName)) return true;
    if (current.tagName === 'INPUT' && INTERACTIVE_INPUT_TYPES.has(current.type)) return true;
    const role = current.getAttribute('role');
    if (role && INTERACTIVE_ROLES.has(role)) return true;
    if (current.hasAttribute('onclick')) return true;
    if (current.hasAttribute('href')) return true;
    try {
      const style = window.getComputedStyle(current);
      if (style.cursor === 'pointer') return true;
    } catch (e) {}
    current = current.parentElement;
    depth++;
  }
  return false;
}

function handleMouseDown(e) {
  if (e.target.closest && e.target.closest('#ux-capturer-fab')) return;
  if (e.target.closest && e.target.closest('#ux-capturer-menu')) return;
  if (e.target.closest && e.target.closest('#ux-region-overlay')) return;
  if (!isRecordingCached) return;

  if (isInteractiveElement(e.target)) {
    const target = e.target.closest('a, button, [role], input, select, summary') || e.target;
    const label = (target.innerText || target.textContent || target.getAttribute('aria-label') || target.getAttribute('title') || target.value || '').trim().substring(0, 80);

    const click = {
      clientX: e.clientX,
      clientY: e.clientY,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      devicePixelRatio: window.devicePixelRatio || 1,
      label,
      tag: target.tagName
    };

    console.log('[UX CS] Interactive element clicked:', target.tagName, label, `(${e.clientX}, ${e.clientY})`);
    try {
      chrome.runtime.sendMessage({ action: 'interactiveClick', click });
    } catch (err) {}
  }
}

// ----- DOM Settle Observer -----
function watchDomSettle() {
  if (mutationObserver) mutationObserver.disconnect();
  if (settleTimer) clearTimeout(settleTimer);

  let structuralScore = 0;

  function onSettle() {
    if (mutationObserver) { mutationObserver.disconnect(); mutationObserver = null; }
    settleTimer = null;
    console.log(`[UX CS] DOM settled — structural score: ${structuralScore}`);
    try { chrome.runtime.sendMessage({ action: 'domSettled', structuralScore }); } catch (err) {}
  }

  mutationObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'childList') {
        structuralScore += mutation.addedNodes.length;
        structuralScore += mutation.removedNodes.length;
      }
    }
    if (settleTimer) clearTimeout(settleTimer);
    settleTimer = setTimeout(onSettle, 1000);
  });

  mutationObserver.observe(document.body || document.documentElement, {
    childList: true, subtree: true, attributes: false, characterData: false
  });

  settleTimer = setTimeout(onSettle, 1000);
}

// ----- Message handler from background.js -----
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log('[UX CS] Received message:', request.action);
  if (request.action === 'initRecording') {
    isRecordingCached = true;
    createFab();
    document.addEventListener('mousedown', handleMouseDown, true);
  } else if (request.action === 'hideFab') {
    hideFab();
  } else if (request.action === 'showFab') {
    showFab();
  } else if (request.action === 'removeFab') {
    removeFab();
    document.removeEventListener('mousedown', handleMouseDown, true);
  } else if (request.action === 'watchDomSettle') {
    watchDomSettle();
  } else if (request.action === 'startRegionSelect') {
    startRegionSelect();
  }
});

// ----- Initialization -----
if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local) {
  console.log('[UX CS] Chrome APIs unavailable. Skipping init.');
} else {
  chrome.storage.local.get(['isRecording'], (result) => {
    if (result.isRecording) {
      isRecordingCached = true;
      createFab();
      document.addEventListener('mousedown', handleMouseDown, true);
    }
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !changes.isRecording) return;
    if (changes.isRecording.newValue) {
      isRecordingCached = true;
      createFab();
      document.addEventListener('mousedown', handleMouseDown, true);
    } else {
      isRecordingCached = false;
      removeFab();
      document.removeEventListener('mousedown', handleMouseDown, true);
    }
  });
}

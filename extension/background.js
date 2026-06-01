// ============================================================
// UX Flow Capturer — Background Service Worker (State Machine)
//
// State model:
//   - State = a unique URL
//   - Each state has at most 1 intro (on load) and 1 outro (before leaving)
//   - Interactions within a state are captured only for structural DOM changes
//   - Typing/form filling is ignored (no childList mutations)
//   - Manual captures (Whole / Cropped) are stored as 'manual' triggers in
//     the current state.
//
// The toolbar badge is the global recording-indicator: a red ● while a flow
// is recording, blank otherwise.
// ============================================================

let isRecording = false;
let sessionId = null;
let stateCounter = 0;
let screenshotCounter = 0;
let recordingTabId = null;
let waitingForNavigation = false;
let navigationTimeout = null;
let preClickUrl = null;
let pendingScreenshot = null; // Held until we know if it's an outro or discard
let pendingClick = null;

// FastAPI server. Update if you change the port in start_server.sh.
const SERVER = 'http://127.0.0.1:8000';
const INTERACTION_THRESHOLD = 2;

// ----- Toolbar icon (recording indicator) -----
//
// Paints the AUX logo into a 32x32 ImageData via OffscreenCanvas, with a
// clean 12px red dot in the top-right corner WHEN recording. The logo
// variant follows the app theme: the white mark on dark theme, the
// dark/blue mark on light theme — matching the popup.

const ICON_LIGHT = 'extension_light.png'; // dark/blue mark — for light theme
const ICON_DARK = 'extension_dark.png';   // white mark — for dark theme

async function _appTheme() {
  try {
    const r = await fetch(`${SERVER}/api/theme`, { cache: 'no-store' });
    if (r.ok) return (await r.json()).theme || 'dark';
  } catch (_) {}
  return 'dark';
}

async function _generateIcon(recording) {
  const size = 32;
  const canvas = new OffscreenCanvas(size, size);
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);

  const theme = await _appTheme();
  const logoFile = theme === 'light' ? ICON_LIGHT : ICON_DARK;
  try {
    const blob = await (await fetch(chrome.runtime.getURL(logoFile))).blob();
    const bmp = await createImageBitmap(blob);
    ctx.drawImage(bmp, 0, 0, size, size);
  } catch (_) {}

  if (recording) {
    // Clean red dot, bottom-right. No border.
    ctx.fillStyle = '#ff3b30';
    const r = 7.8;
    ctx.beginPath();
    ctx.arc(size - 5, size - 5, r, 0, Math.PI * 2);
    ctx.fill();
  }

  return ctx.getImageData(0, 0, size, size);
}

async function setBadgeRecording(active) {
  try {
    // Defensively clear any legacy badge text from a prior install.
    chrome.action.setBadgeText({ text: '' });
    const imageData = await _generateIcon(!!active);
    chrome.action.setIcon({ imageData });
    chrome.action.setTitle({
      title: active ? 'AUX — Recording' : 'AUX',
    });
  } catch (e) {
    console.warn('[AUX BG] setIcon failed:', e);
  }
}

// On service-worker boot, paint the icon according to stored state.
chrome.storage.local.get(['isRecording'], (res) => {
  setBadgeRecording(!!res.isRecording);
});

// Normalize URL for comparison: strip hash, trailing slash, sort query params
function normalizeUrl(url) {
  try {
    const u = new URL(url);
    return u.origin + u.pathname.replace(/\/$/, '');
  } catch (e) {
    return url;
  }
}

// ----- Message handlers -----
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  console.log('[UX BG] Received:', request.action);

  if (request.action === 'startRecording') {
    isRecording = true;
    sessionId = request.sessionId;
    stateCounter = 1;
    screenshotCounter = 0;
    waitingForNavigation = false;
    pendingScreenshot = null;
    setBadgeRecording(true);
    console.log(`[UX BG] Started session: ${sessionId}`);

    fetch(`${SERVER}/api/session/${encodeURIComponent(sessionId)}/recording`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ isRecording: true })
    }).catch(e => console.error('[UX BG] Recording-start signal failed:', e));

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        recordingTabId = tabs[0].id;
        preClickUrl = tabs[0].url;
        console.log(`[UX BG] Recording tab ${recordingTabId}: ${preClickUrl}`);

        chrome.tabs.sendMessage(recordingTabId, { action: 'initRecording' }).catch(() => {});

        setTimeout(() => {
          captureAndSend('intro', tabs[0].url);
        }, 600);
      }
    });

  } else if (request.action === 'stopRecording') {
    const sid = sessionId;
    console.log(`[UX BG] Stopped session: ${sid}`);
    isRecording = false;
    setBadgeRecording(false);

    if (recordingTabId) {
      chrome.tabs.sendMessage(recordingTabId, { action: 'removeFab' }).catch(() => {});
    }

    if (sid) {
      fetch(`${SERVER}/api/session/${encodeURIComponent(sid)}/recording`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ isRecording: false })
      }).catch(e => console.error('[UX BG] Recording-stop signal failed:', e));

      fetch(`${SERVER}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId: sid })
      }).catch(e => console.error('[UX BG] Analyze-marker failed:', e));
    }

    sessionId = null;
    recordingTabId = null;
    pendingScreenshot = null;
    if (navigationTimeout) { clearTimeout(navigationTimeout); navigationTimeout = null; }

  } else if (request.action === 'fabCaptureWhole') {
    // FAB menu → "Whole Screen": same as old fabCapture.
    if (!isRecording) return;
    console.log('[UX BG] Manual capture (whole)');
    const tabUrl = sender.tab ? sender.tab.url : 'unknown';
    captureAndSend('manual', tabUrl);

  } else if (request.action === 'fabCaptureCropped') {
    // FAB menu → "Cropped Screen": ask the content script to start the
    // snipping overlay. The actual capture happens in `regionSelected`.
    if (!sender.tab) return;
    console.log('[UX BG] Manual capture (cropped) requested');
    chrome.tabs.sendMessage(sender.tab.id, { action: 'startRegionSelect' }).catch((e) => {
      console.warn('[UX BG] Could not start region select:', e);
    });

  } else if (request.action === 'regionSelected') {
    // Content script returns the selected rectangle. Capture the visible
    // tab, crop to the rect using OffscreenCanvas, then upload.
    if (!sender.tab) return;
    const rect = request.rect;
    const dpr = request.dpr || 1;
    const tabUrl = sender.tab.url;
    console.log('[UX BG] regionSelected', rect);

    chrome.tabs.sendMessage(sender.tab.id, { action: 'hideFab' }).catch(() => {});

    setTimeout(() => {
      chrome.tabs.captureVisibleTab(null, { format: 'png' }, async (dataUrl) => {
        chrome.tabs.sendMessage(sender.tab.id, { action: 'showFab' }).catch(() => {});
        if (chrome.runtime.lastError || !dataUrl) {
          console.error('[UX BG] capture error:', chrome.runtime.lastError);
          return;
        }
        try {
          const cropped = await cropDataUrl(dataUrl, rect, dpr);
          if (!isRecording || !sessionId) {
            console.warn('[UX BG] Cropped capture ignored — no active recording');
            return;
          }
          screenshotCounter++;
          await uploadCropped(cropped, sessionId, stateCounter, tabUrl, screenshotCounter);
        } catch (e) {
          console.error('[UX BG] crop/upload failed:', e);
        }
      });
    }, 80);

  } else if (request.action === 'interactiveClick') {
    if (!isRecording) return;
    if (waitingForNavigation) {
      console.log('[UX BG] Click ignored — still processing previous');
      return;
    }
    console.log('[UX BG] Interactive click detected');

    const tabUrl = sender.tab ? sender.tab.url : 'unknown';
    const tabId = sender.tab ? sender.tab.id : null;
    waitingForNavigation = true;
    preClickUrl = tabUrl;
    pendingClick = request.click || null;

    captureToMemory((dataUrl) => {
      pendingScreenshot = dataUrl;
      console.log('[UX BG] Pending screenshot captured, watching DOM...');

      if (tabId) {
        chrome.tabs.sendMessage(tabId, { action: 'watchDomSettle' }).catch(() => {});
      }

      if (navigationTimeout) clearTimeout(navigationTimeout);
      navigationTimeout = setTimeout(() => {
        if (waitingForNavigation && isRecording) {
          waitingForNavigation = false;
          console.log('[UX BG] Timeout — checking URL');
          chrome.tabs.get(recordingTabId, (tab) => {
            if (tab && tab.url !== preClickUrl) {
              sendPendingAsOutro(preClickUrl);
              stateCounter++;
              screenshotCounter = 0;
              captureAndSend('intro', tab.url);
            } else {
              console.log('[UX BG] Timeout, no URL change — discarding pending');
              pendingScreenshot = null;
              pendingClick = null;
            }
          });
        }
      }, 3500);
    });

  } else if (request.action === 'domSettled') {
    if (!isRecording || !waitingForNavigation) return;
    waitingForNavigation = false;
    if (navigationTimeout) { clearTimeout(navigationTimeout); navigationTimeout = null; }

    const structuralScore = request.structuralScore || 0;
    const tabUrl = sender.tab ? sender.tab.url : 'unknown';
    const urlChanged = preClickUrl && normalizeUrl(tabUrl) !== normalizeUrl(preClickUrl);

    console.log(`[UX BG] DOM settled — structural: ${structuralScore}, urlChanged: ${urlChanged}`);

    if (urlChanged) {
      sendPendingAsOutro(preClickUrl);
      stateCounter++;
      screenshotCounter = 0;
      console.log(`[UX BG] NEW STATE ${stateCounter} (URL changed)`);
      captureAndSend('intro', tabUrl);
    } else if (structuralScore > INTERACTION_THRESHOLD) {
      const interactionClick = pendingClick;
      const preDataUrl = pendingScreenshot;
      pendingScreenshot = null;
      pendingClick = null;
      console.log(`[UX BG] INTERACTION (structural: ${structuralScore}) — pair`);
      if (preDataUrl) {
        sendCaptured(preDataUrl, 'interaction_pre', tabUrl, interactionClick);
      }
      captureAndSend('interaction_post', tabUrl);
    } else {
      pendingScreenshot = null;
      pendingClick = null;
      console.log(`[UX BG] IGNORED (structural: ${structuralScore} < ${INTERACTION_THRESHOLD})`);
    }
  }
});

// ----- Full page navigation -----
chrome.webNavigation.onCompleted.addListener((details) => {
  if (!isRecording || details.frameId !== 0) return;
  if (details.tabId !== recordingTabId) return;

  console.log(`[UX BG] webNavigation.onCompleted: ${details.url}`);
  const meaningfulChange = preClickUrl && normalizeUrl(details.url) !== normalizeUrl(preClickUrl);

  if (waitingForNavigation && meaningfulChange) {
    waitingForNavigation = false;
    if (navigationTimeout) { clearTimeout(navigationTimeout); navigationTimeout = null; }

    sendPendingAsOutro(preClickUrl);
    stateCounter++;
    screenshotCounter = 0;
    console.log(`[UX BG] Full navigation → NEW STATE ${stateCounter}`);

    setTimeout(() => {
      if (isRecording) {
        captureAndSend('intro', details.url);
        chrome.tabs.sendMessage(recordingTabId, { action: 'initRecording' }).catch(() => {});
      }
    }, 800);
  }
});

// ----- Region cropping (OffscreenCanvas in service worker) -----
async function cropDataUrl(dataUrl, rect, dpr) {
  const blob = await (await fetch(dataUrl)).blob();
  const bitmap = await createImageBitmap(blob);

  const sx = Math.max(0, Math.round(rect.x * dpr));
  const sy = Math.max(0, Math.round(rect.y * dpr));
  const sw = Math.max(1, Math.round(rect.w * dpr));
  const sh = Math.max(1, Math.round(rect.h * dpr));

  const canvas = new OffscreenCanvas(sw, sh);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bitmap, sx, sy, sw, sh, 0, 0, sw, sh);
  const out = await canvas.convertToBlob({ type: 'image/png' });

  return await blobToDataUrl(out);
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

async function uploadCropped(dataUrl, sid, state, url, idx) {
  const screenshotIndex = idx ?? 1;
  const payload = {
    sessionId: sid,
    url,
    trigger: 'manual',
    state,
    screenshotIndex,
    image: dataUrl,
    click: null,
  };
  try {
    await fetch(`${SERVER}/capture`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    console.log('[UX BG] Region capture uploaded');
  } catch (e) {
    console.error('[UX BG] upload failed:', e);
  }
}

// ----- Screenshot helpers -----
function captureToMemory(callback) {
  if (recordingTabId) {
    chrome.tabs.sendMessage(recordingTabId, { action: 'hideFab' }).catch(() => {});
  }
  setTimeout(() => {
    chrome.tabs.captureVisibleTab(null, { format: 'png' }, (dataUrl) => {
      if (recordingTabId) {
        chrome.tabs.sendMessage(recordingTabId, { action: 'showFab' }).catch(() => {});
      }
      if (chrome.runtime.lastError) {
        console.error('[UX BG] Capture error:', chrome.runtime.lastError.message);
        callback(null);
        return;
      }
      callback(dataUrl);
    });
  }, 80);
}

function sendCaptured(dataUrl, trigger, url, click) {
  if (!dataUrl) return;
  screenshotCounter++;
  const payload = {
    sessionId, url, trigger,
    state: stateCounter, screenshotIndex: screenshotCounter,
    image: dataUrl,
    click: click || null
  };
  const filename = `state_${stateCounter}/${String(screenshotCounter).padStart(2, '0')}_${trigger}.png`;
  console.log(`[UX BG] Sending: ${filename}`);
  fetch(`${SERVER}/capture`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).catch(e => console.error('[UX BG] Send failed:', e));
}

function sendPendingAsOutro(url) {
  if (!pendingScreenshot) return;
  sendCaptured(pendingScreenshot, 'outro', url, pendingClick);
  pendingScreenshot = null;
  pendingClick = null;
}

function captureAndSend(trigger, url, callback, click) {
  if (!isRecording && trigger !== 'outro') {
    if (callback) callback();
    return;
  }

  if (recordingTabId) {
    chrome.tabs.sendMessage(recordingTabId, { action: 'hideFab' }).catch(() => {});
  }

  setTimeout(() => {
    chrome.tabs.captureVisibleTab(null, { format: 'png' }, (dataUrl) => {
      if (recordingTabId) {
        chrome.tabs.sendMessage(recordingTabId, { action: 'showFab' }).catch(() => {});
      }
      if (chrome.runtime.lastError) {
        console.error('[UX BG] Capture error:', chrome.runtime.lastError.message);
        if (callback) callback();
        return;
      }

      screenshotCounter++;
      const payload = {
        sessionId, url, trigger,
        state: stateCounter, screenshotIndex: screenshotCounter,
        image: dataUrl,
        click: click || null
      };
      const filename = `state_${stateCounter}/${String(screenshotCounter).padStart(2, '0')}_${trigger}.png`;
      console.log(`[UX BG] Sending: ${filename}`);

      fetch(`${SERVER}/capture`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
        .then(() => { if (callback) callback(); })
        .catch(e => {
          console.error('[UX BG] Send failed:', e);
          if (callback) callback();
        });
    });
  }, 80);
}

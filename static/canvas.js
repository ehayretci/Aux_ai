// =================================================================
// UX Flow Canvas — Figma-style workspace
//
// - Pan with empty-area drag, zoom with wheel/trackpad pinch
// - Frames are draggable from the header
// - Screens are individually draggable inside the world
// - Connections drawn in window-space SVG overlay (always on top,
//   constant pixel thickness regardless of zoom)
// - Polls /api/session/<id> incrementally — preserves drag positions
// - Bottom-centre pill toolbar drives Fit / Zoom / Reset
// - Reusable in-canvas overlay handles per-screen analysis (and is
//   structured to host flow-level analysis in a follow-up)
// =================================================================

const SESSION_ID = window.SESSION_ID;
const POLL_INTERVAL_MS = 1500;
const SCREEN_WIDTH = 360;
const SCREEN_GAP = 16;
const FRAME_GAP = 120;

const viewport = document.getElementById("viewport");
const world = document.getElementById("world");
const framesEl = document.getElementById("frames");
const svg = document.getElementById("connections");
const sessionNameEl = document.getElementById("session-name");
const statusDotEl = document.getElementById("status-dot");
const zoomLabelEl = document.getElementById("zoom-label");

let view = { x: 80, y: 80, scale: 0.6 };
const stateFrames = {};       // stateKey -> { el, headerEl, contentEl, screens: [paths], stateNum }
const screenEls = {};         // path -> { el, data, stateKey }
const framePositions = {};    // stateKey -> { x, y } in world coords
const screenPositions = {};   // path -> { x, y } relative to frame-content
const analyzedPaths = new Set();  // paths that have a saved analysis
const analysisCache = new Map();  // path -> full analysis result

// Last fetched flow analysis (drives the post-analysis button state).
let flowAnalysisResult = null;

sessionNameEl.textContent = SESSION_ID || "(no session selected)";

function applyTransform() {
  world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  if (zoomLabelEl) zoomLabelEl.textContent = Math.round(view.scale * 100) + "%";
  drawConnections();
}
applyTransform();

// ------------------------------------------------------- Pan & zoom -------
let panning = false;
let panStart = { x: 0, y: 0, vx: 0, vy: 0 };

viewport.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  if (e.target !== viewport && e.target !== world && e.target !== framesEl) return;
  panning = true;
  viewport.classList.add("panning");
  panStart = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
});
window.addEventListener("mouseup", () => {
  if (panning) {
    panning = false;
    viewport.classList.remove("panning");
  }
});
window.addEventListener("mousemove", (e) => {
  if (!panning) return;
  view.x = panStart.vx + (e.clientX - panStart.x);
  view.y = panStart.vy + (e.clientY - panStart.y);
  applyTransform();
});

viewport.addEventListener("wheel", (e) => {
  e.preventDefault();
  zoomBy(Math.exp(-e.deltaY * (e.ctrlKey ? 0.02 : 0.0015)),
         e.clientX, e.clientY);
}, { passive: false });

function zoomBy(factor, anchorX, anchorY) {
  const rect = viewport.getBoundingClientRect();
  const ax = (anchorX ?? rect.left + rect.width / 2) - rect.left;
  const ay = (anchorY ?? rect.top + rect.height / 2) - rect.top;
  const wx = (ax - view.x) / view.scale;
  const wy = (ay - view.y) / view.scale;
  let newScale = view.scale * factor;
  newScale = Math.max(0.05, Math.min(newScale, 4));
  view.scale = newScale;
  view.x = ax - wx * newScale;
  view.y = ay - wy * newScale;
  applyTransform();
}

// ------------------------------------------------ Bottom-pill controls ----
document.getElementById("fit-btn").addEventListener("click", fitToContent);
document.getElementById("zoom-in-btn").addEventListener("click", () => zoomBy(1.2));
document.getElementById("zoom-out-btn").addEventListener("click", () => zoomBy(1 / 1.2));
document.getElementById("reset-btn").addEventListener("click", () => {
  view = { x: 80, y: 80, scale: 0.6 };
  applyTransform();
});

const analyseFlowBtn = document.getElementById("analyse-flow-btn");
if (analyseFlowBtn) analyseFlowBtn.addEventListener("click", onAnalyseFlowClick);

// --------------------------------------------- Inline rename: flow name --
sessionNameEl.addEventListener("click", () => beginInlineEdit(sessionNameEl, async (newName) => {
  if (!newName || !SESSION_ID) return;
  try {
    const fm = await fetchFlowMetadata();
    fm.flow_name = newName;
    await fetch(`/api/session/${encodeURIComponent(SESSION_ID)}/flow_metadata`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fm),
    });
  } catch (e) { console.warn("rename flow failed", e); }
}));

async function fetchFlowMetadata() {
  try {
    const res = await fetch(`/api/session/${encodeURIComponent(SESSION_ID)}`);
    const data = await res.json();
    return data.flowMetadata || {};
  } catch (_) { return {}; }
}

function beginInlineEdit(el, onCommit) {
  if (el.dataset.editing === "1") return;
  el.dataset.editing = "1";
  const original = el.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.value = original;
  input.className = "inline-edit-input";
  el.replaceWith(input);
  input.focus();
  input.select();

  function finish(commit) {
    const value = input.value.trim();
    el.textContent = commit && value ? value : original;
    el.dataset.editing = "0";
    input.replaceWith(el);
    if (commit && value && value !== original) onCommit(value);
  }
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

function fitToContent() {
  const keys = Object.keys(stateFrames);
  if (!keys.length) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const k of keys) {
    const f = stateFrames[k];
    const fp = framePositions[k];
    const w = f.el.offsetWidth;
    const h = f.el.offsetHeight;
    minX = Math.min(minX, fp.x);
    minY = Math.min(minY, fp.y);
    maxX = Math.max(maxX, fp.x + w);
    maxY = Math.max(maxY, fp.y + h);
  }
  const pad = 80;
  const contentW = (maxX - minX) + pad * 2;
  const contentH = (maxY - minY) + pad * 2;
  const vw = viewport.clientWidth;
  const vh = viewport.clientHeight;
  const scale = Math.min(vw / contentW, vh / contentH, 1);
  view.scale = scale;
  view.x = (vw - (maxX - minX) * scale) / 2 - minX * scale;
  view.y = (vh - (maxY - minY) * scale) / 2 - minY * scale;
  applyTransform();
}

// ----------------------------------------------------- Polling & rendering
async function pollSession() {
  if (!SESSION_ID) {
    setRecording(false);
    return;
  }
  try {
    const res = await fetch(`/api/session/${encodeURIComponent(SESSION_ID)}`);
    if (!res.ok) {
      setRecording(false);
      return;
    }
    const data = await res.json();
    renderIncremental(data);
    setRecording(!!data.isRecording);

    // Track flow-analysis cache so the top-right button can switch state.
    if (data.flowAnalysis && data.flowAnalysis.journey_score != null) {
      flowAnalysisResult = data.flowAnalysis;
      analyseFlowBtn?.classList.add("has-result");
      const lbl = analyseFlowBtn?.querySelector(".flow-btn-label");
      if (lbl) lbl.textContent = "Flow Analysis";
    }
  } catch (e) {
    setRecording(false);
  }
}

function setRecording(active) {
  if (statusDotEl) statusDotEl.dataset.recording = active ? "true" : "false";
}

function stateNum(stateKey) {
  return parseInt(stateKey.replace(/\D/g, "")) || 0;
}

function renderIncremental(data) {
  if (Array.isArray(data.analyzedPaths)) {
    for (const p of data.analyzedPaths) analyzedPaths.add(p);
  }

  const fmName = (data.flowMetadata || {}).flow_name;
  if (fmName && sessionNameEl.dataset.editing !== "1" && sessionNameEl.textContent !== fmName) {
    sessionNameEl.textContent = fmName;
  } else if (!fmName && sessionNameEl.dataset.editing !== "1" && SESSION_ID) {
    sessionNameEl.textContent = SESSION_ID;
  }

  const stateKeys = Object.keys(data.states || {}).sort(
    (a, b) => stateNum(a) - stateNum(b)
  );

  for (const key of stateKeys) {
    const stateData = data.states[key];
    let frame = stateFrames[key];

    if (!frame) {
      frame = createFrame(key, stateData);
      stateFrames[key] = frame;
      framesEl.appendChild(frame.el);

      if (!framePositions[key]) {
        framePositions[key] = autoFramePosition(key);
      }
      attachFrameDrag(frame, key);
    } else {
      frame.urlEl.textContent = stateData.url || "";
      const wantName = stateData.name || `State ${stateData.state}`;
      if (frame.titleEl && frame.titleEl.dataset.editing !== "1" && frame.stateName !== wantName) {
        frame.titleEl.textContent = wantName;
        frame.stateName = wantName;
      }
    }

    const sortedScreens = [...stateData.screenshots].sort((a, b) => {
      const order = (s) => ({
        intro: 0,
        interaction: 1, manual: 1, interaction_pre: 1, interaction_post: 1,
        outro: 2,
      }[s.trigger] ?? 1);
      if (order(a) !== order(b)) return order(a) - order(b);
      return a.index - b.index;
    });

    for (const s of sortedScreens) {
      if (!screenEls[s.path]) {
        const screenEl = createScreen(s, key);
        frame.contentEl.appendChild(screenEl);
        frame.screens.push(s.path);
        screenEls[s.path] = { el: screenEl, data: s, stateKey: key };
        attachScreenDrag(screenEl, s.path, key);

        if (!screenPositions[s.path]) {
          screenPositions[s.path] = autoScreenPosition(key, s);
        }
      } else {
        const meta = screenEls[s.path];
        meta.data = s;
        const wrap = meta.el.querySelector(".screen-img-wrap");
        if (s.click && hasClickMarker(s.trigger) && wrap && !wrap.querySelector(".click-marker")) {
          const marker = document.createElement("div");
          marker.className = "click-marker";
          marker.dataset.click = JSON.stringify(s.click);
          wrap.appendChild(marker);
        }
      }

      if (analyzedPaths.has(s.path)) {
        screenEls[s.path]?.el.classList.add("analyzed");
      }
    }

    layoutFrame(key);
  }

  drawConnections();
}

function autoFramePosition(stateKey) {
  const others = Object.keys(stateFrames).filter(k => k !== stateKey);
  if (!others.length) return { x: 0, y: 0 };
  let maxRight = 0, topY = 0;
  for (const k of others) {
    const fp = framePositions[k];
    const f = stateFrames[k];
    if (!fp || !f) continue;
    const right = fp.x + f.el.offsetWidth;
    if (right > maxRight) {
      maxRight = right;
      topY = fp.y;
    }
  }
  return { x: maxRight + FRAME_GAP, y: topY };
}

function hasClickMarker(trigger) {
  return trigger === "outro" || trigger === "interaction_pre" || trigger === "interaction";
}

function estimatedScreenHeight(el) {
  return el?.offsetHeight || (SCREEN_WIDTH * 9 / 16);
}

function autoScreenPosition(stateKey, screenData) {
  const frame = stateFrames[stateKey];

  if (screenData && screenData.trigger === "interaction_post") {
    const preIndex = screenData.index - 1;
    const prePath = frame.screens.find((p) => {
      const m = screenEls[p];
      return m && m.data.index === preIndex && m.data.trigger === "interaction_pre";
    });
    if (prePath) {
      const prePos = screenPositions[prePath];
      const preEl = screenEls[prePath].el;
      if (prePos) {
        return {
          x: prePos.x + (preEl.offsetWidth || SCREEN_WIDTH) + SCREEN_GAP,
          y: prePos.y,
        };
      }
    }
  }

  if (screenData && screenData.trigger === "intro") return { x: 0, y: 0 };

  let maxBottom = 0;
  for (const path of frame.screens) {
    if (path === screenData?.path) continue;
    const sp = screenPositions[path];
    const el = screenEls[path]?.el;
    if (!sp || !el) continue;
    const bottom = sp.y + estimatedScreenHeight(el);
    if (bottom > maxBottom) maxBottom = bottom;
  }
  return { x: 0, y: maxBottom > 0 ? maxBottom + SCREEN_GAP : 0 };
}

function createFrame(stateKey, stateData) {
  const el = document.createElement("div");
  el.className = "state-frame";
  el.dataset.stateKey = stateKey;

  const headerEl = document.createElement("div");
  headerEl.className = "frame-header";
  const titleEl = document.createElement("span");
  titleEl.className = "frame-title";
  titleEl.title = "Click to rename";
  titleEl.textContent = stateData.name || `State ${stateData.state}`;
  titleEl.addEventListener("mousedown", (e) => e.stopPropagation());
  titleEl.addEventListener("click", (e) => {
    e.stopPropagation();
    beginInlineEdit(titleEl, async (newName) => {
      try {
        await fetch(`/api/session/${encodeURIComponent(SESSION_ID)}/state/${encodeURIComponent(stateKey)}/rename`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newName }),
        });
        if (stateFrames[stateKey]) stateFrames[stateKey].stateName = newName;
      } catch (err) { console.warn("rename state failed", err); }
    });
  });
  const urlEl = document.createElement("span");
  urlEl.className = "frame-url";
  urlEl.textContent = stateData.url || "";
  headerEl.appendChild(titleEl);
  headerEl.appendChild(urlEl);

  const contentEl = document.createElement("div");
  contentEl.className = "frame-content";

  el.appendChild(headerEl);
  el.appendChild(contentEl);

  return {
    el, headerEl, urlEl, titleEl, contentEl,
    screens: [],
    stateNum: stateData.state,
    stateName: stateData.name || `State ${stateData.state}`,
  };
}

function createScreen(s, stateKey) {
  const screen = document.createElement("div");
  screen.className = "screen " + s.trigger;
  screen.dataset.path = s.path;
  screen.dataset.trigger = s.trigger;

  // Wrapper element so the click marker tracks the rendered image.
  const wrap = document.createElement("div");
  wrap.className = "screen-img-wrap";
  const img = document.createElement("img");
  img.src = "/screenshots/" + s.path;
  img.addEventListener("load", () => {
    layoutFrame(stateKey);
    drawConnections();
  });
  wrap.appendChild(img);
  screen.appendChild(wrap);

  const label = document.createElement("div");
  label.className = "screen-label";
  label.textContent = triggerLabel(s.trigger);
  screen.appendChild(label);

  if (s.click && hasClickMarker(s.trigger)) {
    const marker = document.createElement("div");
    marker.className = "click-marker";
    marker.dataset.click = JSON.stringify(s.click);
    wrap.appendChild(marker);
  }

  const analyzeBtn = document.createElement("button");
  analyzeBtn.className = "analyze-btn";
  analyzeBtn.textContent = "🔍";
  analyzeBtn.title = "Run UI analysis on this screen";
  analyzeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    e.preventDefault();
    analyzeScreenshot(s.path);
  });
  screen.appendChild(analyzeBtn);

  return screen;
}

function triggerLabel(trigger) {
  switch (trigger) {
    case "interaction_pre": return "click";
    case "interaction_post": return "result";
    case "interaction": return "interaction";
    default: return trigger;
  }
}

/**
 * Walk a frame's screens in flow order and push each row down so that the
 * gap between consecutive rows is at least SCREEN_GAP. Preserves user
 * customisation: only pushes rows DOWN, never pulls them up.
 *
 * "Row" = a single screen, OR an interaction_pre/post pair sharing a y.
 */
function enforceFrameGaps(stateKey) {
  const frame = stateFrames[stateKey];
  if (!frame) return;

  const visited = new Set();
  const rows = [];
  for (const path of frame.screens) {
    if (visited.has(path)) continue;
    const meta = screenEls[path];
    if (!meta) continue;

    const trig = meta.data.trigger;
    if (trig === "interaction_pre") {
      const post = frame.screens.find((p) => {
        const m = screenEls[p];
        return m && m.data.trigger === "interaction_post"
            && m.data.index === meta.data.index + 1;
      });
      if (post) {
        rows.push([path, post]);
        visited.add(path); visited.add(post);
        continue;
      }
    } else if (trig === "interaction_post") {
      const pre = frame.screens.find((p) => {
        const m = screenEls[p];
        return m && m.data.trigger === "interaction_pre"
            && m.data.index === meta.data.index - 1;
      });
      if (pre) {
        rows.push([pre, path]);
        visited.add(pre); visited.add(path);
        continue;
      }
    }
    rows.push([path]);
    visited.add(path);
  }

  // Sort rows by their current top-y so user re-ordering is respected.
  rows.sort((a, b) => {
    const ay = (screenPositions[a[0]] || {}).y || 0;
    const by = (screenPositions[b[0]] || {}).y || 0;
    return ay - by;
  });

  let prevBottom = 0;
  let isFirst = true;
  for (const row of rows) {
    const minY = isFirst ? 0 : prevBottom + SCREEN_GAP;
    const curY = (screenPositions[row[0]] || {}).y ?? 0;
    if (curY < minY) {
      for (const p of row) {
        const sp = screenPositions[p] || { x: 0, y: 0 };
        screenPositions[p] = { x: sp.x ?? 0, y: minY };
      }
    }
    let rowBottom = 0;
    for (const p of row) {
      const sp = screenPositions[p];
      const el = screenEls[p]?.el;
      const h = el?.offsetHeight || (SCREEN_WIDTH * 0.6);
      const b = (sp?.y || 0) + h;
      if (b > rowBottom) rowBottom = b;
    }
    prevBottom = rowBottom;
    isFirst = false;
  }
}

function layoutFrame(stateKey) {
  const frame = stateFrames[stateKey];
  if (!frame) return;
  enforceFrameGaps(stateKey);
  const fp = framePositions[stateKey];
  frame.el.style.left = fp.x + "px";
  frame.el.style.top = fp.y + "px";

  let maxX = 0, maxY = 0;
  for (const path of frame.screens) {
    const sp = screenPositions[path];
    const meta = screenEls[path];
    if (!sp || !meta) continue;
    meta.el.style.left = sp.x + "px";
    meta.el.style.top = sp.y + "px";

    const marker = meta.el.querySelector(".click-marker");
    const img = meta.el.querySelector("img");
    if (marker && img && img.complete && img.naturalWidth) {
      const click = JSON.parse(marker.dataset.click);
      const w = img.clientWidth, h = img.clientHeight;
      const x = (click.clientX || 0) * (w / (click.viewportWidth || w));
      const y = (click.clientY || 0) * (h / (click.viewportHeight || h));
      marker.style.left = x + "px";
      marker.style.top = y + "px";
    }

    maxX = Math.max(maxX, sp.x + (meta.el.offsetWidth || SCREEN_WIDTH));
    maxY = Math.max(maxY, sp.y + (meta.el.offsetHeight || SCREEN_WIDTH * 0.6));
  }
  frame.contentEl.style.width = Math.max(maxX, 280) + "px";
  frame.contentEl.style.height = Math.max(maxY, 80) + "px";
}

// -------------------------------------------------------- Drag handlers --
function attachFrameDrag(frame, stateKey) {
  frame.headerEl.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    frame.el.classList.add("dragging");

    const start = { x: e.clientX, y: e.clientY };
    const initial = { ...framePositions[stateKey] };

    function move(ev) {
      const dx = (ev.clientX - start.x) / view.scale;
      const dy = (ev.clientY - start.y) / view.scale;
      framePositions[stateKey] = { x: initial.x + dx, y: initial.y + dy };
      layoutFrame(stateKey);
      drawConnections();
    }
    function up() {
      frame.el.classList.remove("dragging");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      persistLayout();
    }
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
}

function attachScreenDrag(screenEl, path, stateKey) {
  screenEl.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (e.target.classList.contains("analyze-btn")) return;
    e.stopPropagation();
    e.preventDefault();

    const start = { x: e.clientX, y: e.clientY };
    const initial = { ...screenPositions[path] };
    let didMove = false;

    function move(ev) {
      const dx = (ev.clientX - start.x) / view.scale;
      const dy = (ev.clientY - start.y) / view.scale;
      if (!didMove && (Math.abs(dx) > 2 || Math.abs(dy) > 2)) {
        didMove = true;
        screenEl.classList.add("dragging");
      }
      screenPositions[path] = {
        x: Math.max(0, initial.x + dx),
        y: Math.max(0, initial.y + dy),
      };
      layoutFrame(stateKey);
      drawConnections();
    }
    function up() {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      if (didMove) {
        screenEl.classList.remove("dragging");
        persistLayout();
      } else if (analyzedPaths.has(path)) {
        // Click without drag on an already-analysed screen → open overlay.
        openScreenOverlay(path);
      }
    }
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  });
}

// ------------------------------------------- Layout persistence ----------
function layoutKey() { return `ux-canvas-layout:${SESSION_ID}`; }

function persistLayout() {
  try {
    const data = { framePositions, screenPositions };
    localStorage.setItem(layoutKey(), JSON.stringify(data));
  } catch (e) {}
}

function restoreLayout() {
  try {
    const raw = localStorage.getItem(layoutKey());
    if (!raw) return;
    const data = JSON.parse(raw);
    Object.assign(framePositions, data.framePositions || {});
    Object.assign(screenPositions, data.screenPositions || {});
  } catch (e) {}
}

// ------------------------------------------- Connections (SVG overlay) --
function drawConnections() {
  const defs = svg.querySelector("defs");
  while (svg.lastChild && svg.lastChild !== defs) {
    svg.removeChild(svg.lastChild);
  }

  const stateKeys = Object.keys(stateFrames).sort(
    (a, b) => stateNum(a) - stateNum(b)
  );

  for (let i = 0; i < stateKeys.length - 1; i++) {
    const cur = stateFrames[stateKeys[i]];
    const nxt = stateFrames[stateKeys[i + 1]];
    if (!cur || !nxt) continue;

    const outroPath = cur.screens.find(p => screenEls[p]?.data.trigger === "outro");
    const introPath = nxt.screens.find(p => screenEls[p]?.data.trigger === "intro");
    if (!outroPath || !introPath) continue;
    drawScreenConnection(outroPath, introPath);
  }

  for (const key of stateKeys) {
    const frame = stateFrames[key];
    for (const path of frame.screens) {
      const meta = screenEls[path];
      if (!meta || meta.data.trigger !== "interaction_pre") continue;
      const postPath = frame.screens.find((p) => {
        const m = screenEls[p];
        return m && m.data.trigger === "interaction_post" && m.data.index === meta.data.index + 1;
      });
      if (!postPath) continue;
      drawScreenConnection(path, postPath);
    }
  }
}

function drawScreenConnection(fromPath, toPath) {
  const fromEl = screenEls[fromPath]?.el;
  const toEl = screenEls[toPath]?.el;
  if (!fromEl || !toEl) return;
  const marker = fromEl.querySelector(".click-marker");
  const start = marker ? rectCenter(marker) : rightCenter(fromEl);
  const end = leftCenter(toEl);
  drawArrow(start, end);
}

function rectCenter(el) {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}
function rightCenter(el) {
  const r = el.getBoundingClientRect();
  return { x: r.right, y: r.top + r.height / 2 };
}
function leftCenter(el) {
  const r = el.getBoundingClientRect();
  return { x: r.left, y: r.top + r.height / 2 };
}

function drawArrow(start, end) {
  const dx = end.x - start.x;
  const c1 = { x: start.x + dx * 0.4, y: start.y };
  const c2 = { x: end.x - dx * 0.4, y: end.y };
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", `M ${start.x} ${start.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`);
  path.setAttribute("stroke", "#ff3b30");
  path.setAttribute("stroke-width", "2.5");
  path.setAttribute("fill", "none");
  path.setAttribute("marker-end", "url(#arrow)");
  path.setAttribute("opacity", "0.9");
  svg.appendChild(path);
}

// ====================================================================
// SSE loader (kicked off when an analysis starts)
// ====================================================================
const loaderEl = document.getElementById("analysis-loader");
const loaderTitleEl = document.getElementById("loader-title");
const loaderSubtitleEl = document.getElementById("loader-subtitle");
const loaderStepsEl = document.getElementById("loader-steps");
const loaderCancelBtn = document.getElementById("loader-cancel");

let activeSse = null;
let cancelRequested = false;
// Tracks which loader is currently visible so SSE events route to the
// right step-progress UI: "screen" for the per-screen pipeline, "flow"
// for the flow analysis pipeline. null when no loader is open.
let activeLoaderKind = null;

if (loaderCancelBtn) {
  loaderCancelBtn.addEventListener("click", () => {
    cancelRequested = true;
    closeLoader();
  });
}

function openLoader({ title, subtitle, includeFlow = false }) {
  cancelRequested = false;
  activeLoaderKind = "screen";
  loaderTitleEl.textContent = title || "Analysing…";
  loaderSubtitleEl.textContent = subtitle || "This usually takes 30–60 seconds.";
  loaderEl.classList.remove("hidden");
  loaderEl.setAttribute("aria-hidden", "false");

  // Reset every step. Specialist steps start hidden — the orchestrator
  // tells us which to show via the `agents_selected` SSE event. Until that
  // arrives we only show complexity_assessment as active.
  const SPECIALIST_KEYS = new Set([
    "usability", "information_architecture", "visual_design",
    "onboarding", "accessibility",
  ]);
  for (const li of loaderStepsEl.querySelectorAll("li")) {
    li.classList.remove("active", "complete", "skipped");
    const step = li.dataset.step;
    if (step === "flow_analysis") {
      li.classList.toggle("hidden", !includeFlow);
    } else if (SPECIALIST_KEYS.has(step)) {
      // Hide all specialists initially. agents_selected will reveal the right ones.
      li.classList.add("hidden");
    } else {
      li.classList.remove("hidden");
    }
  }
  const first = loaderStepsEl.querySelector('li:not(.hidden)');
  if (first) first.classList.add("active");
}

/**
 * Apply the orchestrator's agents-selected decision to the loader UI:
 *  - reveal each agent in `agents_to_run`
 *  - reveal each agent in `agents_skipped` but mark it `.skipped` (greyed-out)
 *  - if no step is currently active, activate the first non-completed step
 */
function applyAgentsSelected({ agents_to_run = [], agents_skipped = [] }) {
  const order = [
    "usability", "information_architecture", "visual_design",
    "onboarding", "accessibility",
  ];
  const skippedSet = new Map(
    (agents_skipped || []).map(s => [s.agent || s, s.reason || ""])
  );
  for (const k of order) {
    const li = loaderStepsEl.querySelector(`li[data-step="${k}"]`);
    if (!li) continue;
    if (agents_to_run.includes(k)) {
      li.classList.remove("hidden", "skipped");
    } else if (skippedSet.has(k)) {
      li.classList.remove("hidden");
      li.classList.add("skipped");
      // Make sure a skipped step never gets stuck as "active".
      li.classList.remove("active");
    } else {
      li.classList.add("hidden");
    }
  }

  // If the active step has been hidden, promote the next visible non-skipped
  // step to active so the loader UI keeps moving.
  const activeLi = loaderStepsEl.querySelector("li.active");
  if (!activeLi || activeLi.classList.contains("hidden") || activeLi.classList.contains("skipped")) {
    if (activeLi) activeLi.classList.remove("active");
    const next = loaderStepsEl.querySelector("li:not(.hidden):not(.skipped):not(.complete)");
    if (next) next.classList.add("active");
  }
}

function setStepComplete(step) {
  const li = loaderStepsEl.querySelector(`li[data-step="${step}"]`);
  if (!li) return;
  li.classList.remove("active");
  li.classList.add("complete");
  let cur = li.nextElementSibling;
  while (cur) {
    const c = cur.classList;
    if (!c.contains("hidden") && !c.contains("complete") && !c.contains("skipped")) {
      cur.classList.add("active");
      break;
    }
    cur = cur.nextElementSibling;
  }
}

function closeLoader() {
  loaderEl.classList.add("hidden");
  loaderEl.setAttribute("aria-hidden", "true");
  if (activeLoaderKind === "screen") activeLoaderKind = null;
  if (activeSse) { activeSse.close(); activeSse = null; }
}

function subscribeProgress(sessionId) {
  if (!sessionId) return null;
  if (activeSse) activeSse.close();
  const es = new EventSource(`/analyse/flow/progress/${encodeURIComponent(sessionId)}`);
  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);

      // Typed events (orchestrator handed us a plan, etc.) — only meaningful
      // for the screen pipeline.
      if (data.event === "agents_selected") {
        if (activeLoaderKind === "screen") {
          applyAgentsSelected({
            agents_to_run: data.agents_to_run || [],
            agents_skipped: data.agents_skipped || [],
          });
        }
        return;
      }

      if (!data.completed || !data.step) return;

      // Route step events to whichever loader is open. Flow steps and
      // screen steps share the SSE channel; we discriminate by step key
      // first (so a stale event from a previous run can't drive the wrong
      // loader) and by activeLoaderKind second.
      if (FLOW_STEP_INDEX.hasOwnProperty(data.step)) {
        if (activeLoaderKind === "flow") flowStepComplete(data.step);
        return;
      }
      if (activeLoaderKind === "screen") setStepComplete(data.step);
    } catch (e) {}
  };
  es.onerror = () => { /* keep open — server may still be running */ };
  activeSse = es;
  return es;
}

// ====================================================================
// Analyse a single screenshot — render result in the in-canvas overlay
// ====================================================================
async function analyzeScreenshot(path) {
  if (!SESSION_ID) {
    console.warn("[Analyse] No session — cannot analyse");
    return;
  }
  const meta = screenEls[path];
  const labelText = labelForPath(path);

  // If we already have a cached result, jump straight to the overlay.
  if (analyzedPaths.has(path) && analysisCache.get(path)) {
    openScreenOverlay(path);
    return;
  }

  openLoader({
    title: "Analysing screen…",
    subtitle: labelText,
    includeFlow: false,
  });
  subscribeProgress(SESSION_ID);

  try {
    const result = await runScreenAnalysis(path);
    analysisCache.set(path, result);
    analyzedPaths.add(path);
    screenEls[path]?.el.classList.add("analyzed");

    if (cancelRequested) { closeLoader(); return; }

    closeLoader();
    openScreenOverlay(path);
  } catch (err) {
    console.error("[Analyse]", err);
    loaderTitleEl.textContent = "Analysis failed";
    loaderSubtitleEl.textContent = err.message || "Something went wrong.";
  }
}

// Pure analysis call — used both by the button click and by overlay
// arrow navigation when a sibling screen hasn't been analysed yet.
async function runScreenAnalysis(path) {
  const meta = screenEls[path];
  const fm = await fetchFlowMetadata();
  const response = await fetch("/analyse/screen", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: SESSION_ID,
      screenshot_path: path,
      screen_id: path,
      state_name: meta ? (stateFrames[meta.stateKey]?.stateName || meta.stateKey) : null,
      screen_number: meta ? (stateFrames[meta.stateKey]?.screens?.indexOf(path) + 1 || meta.data.index) : null,
      flow_metadata: fm,
    }),
  });
  if (!response.ok) throw new Error(`Server error: ${response.status}`);
  return await response.json();
}

function labelForPath(path) {
  const meta = screenEls[path];
  if (!meta) return path;
  const frame = stateFrames[meta.stateKey];
  const state = frame ? (frame.stateName || `State ${frame.stateNum}`) : meta.stateKey;
  const idx = (frame?.screens || []).indexOf(path);
  return `${state} — Screen ${idx >= 0 ? idx + 1 : meta.data.index}`;
}

// ====================================================================
// Flow analysis: distinct loader + in-canvas overlay (NO new tab)
// ====================================================================
const FLOW_STEP_ORDER = [
  "preparing_context",
  "objective_analysis",
  "contextual_analysis",
  "synthesising",
  "generating_report",
];
const FLOW_STEP_INDEX = Object.fromEntries(
  FLOW_STEP_ORDER.map((k, i) => [k, i])
);

const flowLoaderEl = document.getElementById("flow-loader");
const flowLoaderStepsEl = document.getElementById("flow-loader-steps");
const flowLoaderFlowName = document.getElementById("flow-loader-flow-name");
const flowLoaderStepNum = document.getElementById("flow-loader-step-num");
const flowLoaderCancel = document.getElementById("flow-loader-cancel");

const flowOverlayEl = document.getElementById("flow-overlay");
const flowOverlayBody = document.getElementById("flow-overlay-body");
const flowOverlayTitle = document.getElementById("flow-overlay-title");
const flowOverlaySubtitle = document.getElementById("flow-overlay-subtitle");
const flowOverlayCloseBtn = document.getElementById("flow-overlay-close");

let flowReceivedSteps = new Set();   // step keys we've heard about
let flowEmittedIndex = 0;             // next step index in FLOW_STEP_ORDER to commit

if (flowLoaderCancel) {
  flowLoaderCancel.addEventListener("click", () => {
    cancelRequested = true;
    closeFlowLoader();
  });
}
flowOverlayCloseBtn.addEventListener("click", closeFlowOverlay);
flowOverlayEl.addEventListener("click", (e) => {
  if (e.target?.dataset?.overlayClose !== undefined) closeFlowOverlay();
});

function openFlowLoader({ flowName }) {
  cancelRequested = false;
  activeLoaderKind = "flow";
  flowReceivedSteps = new Set();
  flowEmittedIndex = 0;

  flowLoaderFlowName.textContent = flowName || "Untitled flow";
  flowLoaderStepNum.textContent = "1";
  for (const li of flowLoaderStepsEl.querySelectorAll("li")) {
    li.classList.remove("active", "complete");
  }
  // Activate the first step.
  const first = flowLoaderStepsEl.querySelector('li[data-step="preparing_context"]');
  if (first) first.classList.add("active");

  flowLoaderEl.classList.remove("hidden");
  flowLoaderEl.setAttribute("aria-hidden", "false");
}

function closeFlowLoader() {
  flowLoaderEl.classList.add("hidden");
  flowLoaderEl.setAttribute("aria-hidden", "true");
  if (activeLoaderKind === "flow") activeLoaderKind = null;
  if (activeSse) { activeSse.close(); activeSse = null; }
}

/**
 * Mark a flow step complete. Steps emit in strict FLOW_STEP_ORDER even if
 * they arrive out of order: a later-arriving step waits in `flowReceivedSteps`
 * until the gap is filled, then they all flush in order.
 */
function flowStepComplete(step) {
  if (!FLOW_STEP_INDEX.hasOwnProperty(step)) return;
  flowReceivedSteps.add(step);

  while (
    flowEmittedIndex < FLOW_STEP_ORDER.length
    && flowReceivedSteps.has(FLOW_STEP_ORDER[flowEmittedIndex])
  ) {
    const k = FLOW_STEP_ORDER[flowEmittedIndex];
    const li = flowLoaderStepsEl.querySelector(`li[data-step="${k}"]`);
    if (li) {
      li.classList.remove("active");
      li.classList.add("complete");
    }
    flowEmittedIndex++;
  }

  // Promote the next pending step to active. Step counter shows the
  // 1-indexed position of whichever step is currently in progress.
  if (flowEmittedIndex < FLOW_STEP_ORDER.length) {
    const next = FLOW_STEP_ORDER[flowEmittedIndex];
    const li = flowLoaderStepsEl.querySelector(`li[data-step="${next}"]`);
    if (li) li.classList.add("active");
    flowLoaderStepNum.textContent = String(flowEmittedIndex + 1);
  } else {
    flowLoaderStepNum.textContent = "5";
  }
}

// "Analyse Flow" top-bar button -------------------------------------------
async function onAnalyseFlowClick() {
  if (!SESSION_ID) return;

  // Post-analysis: re-open the existing in-canvas overlay with cached data.
  if (flowAnalysisResult && analyseFlowBtn?.classList.contains("has-result")) {
    const fm = await fetchFlowMetadata();
    openFlowOverlay({ flow_analysis: flowAnalysisResult, flow_metadata: fm });
    return;
  }

  const screens = collectFlowScreens();
  if (!screens.length) { alert("No screens captured yet."); return; }

  const fm = await fetchFlowMetadata();
  openFlowLoader({ flowName: fm.flow_name || SESSION_ID });
  subscribeProgress(SESSION_ID);

  try {
    const response = await fetch("/analyse/flow", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID, screens, flow_metadata: fm }),
    });
    if (!response.ok) throw new Error(`Server error: ${response.status}`);
    const result = await response.json();

    if (cancelRequested) { closeFlowLoader(); return; }

    flowAnalysisResult = result.flow_analysis || null;

    // Update top-right button to post-analysis state.
    if (flowAnalysisResult) {
      analyseFlowBtn?.classList.add("has-result");
      const lbl = analyseFlowBtn?.querySelector(".flow-btn-label");
      if (lbl) lbl.textContent = "Flow Analysis";
    }

    closeFlowLoader();
    openFlowOverlay(result);
  } catch (err) {
    console.error("[FlowAnalyse]", err);
    flowLoaderFlowName.textContent = "Flow analysis failed";
    // Keep the loader open so the user can read the error then cancel.
    const ul = flowLoaderStepsEl;
    if (ul) ul.innerHTML = `<li class="active" style="color:#ff3b30">${escapeHtml(err.message || "Something went wrong.")}</li>`;
  }
}

function collectFlowScreens() {
  const out = [];
  const stateKeys = Object.keys(stateFrames).sort((a, b) => stateNum(a) - stateNum(b));
  let runningNum = 0;
  for (const sk of stateKeys) {
    const frame = stateFrames[sk];
    if (!frame) continue;
    const sortedPaths = [...frame.screens]
      .map((p) => screenEls[p])
      .filter(Boolean)
      .sort((a, b) => (a.data.index || 0) - (b.data.index || 0));
    sortedPaths.forEach((meta) => {
      runningNum++;
      out.push({
        screenshot_path: meta.data.path,
        screen_id: meta.data.path,
        state_name: frame.stateName || `State ${frame.stateNum}`,
        screen_number: runningNum,
      });
    });
  }
  return out;
}

// ====================================================================
// Flow analysis OVERLAY (replaces the old window.open() report)
// ====================================================================
function openFlowOverlay(payload) {
  flowOverlayEl.classList.remove("hidden");
  flowOverlayEl.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  renderFlowAnalysisIntoOverlay(payload);
}

function closeFlowOverlay() {
  flowOverlayEl.classList.add("hidden");
  flowOverlayEl.setAttribute("aria-hidden", "true");
  // Only release scroll lock if the screen overlay isn't also open.
  if (overlayEl.classList.contains("hidden")) document.body.style.overflow = "";
}

window.addEventListener("keydown", (e) => {
  if (!flowOverlayEl.classList.contains("hidden") && e.key === "Escape") {
    closeFlowOverlay();
  }
});

// Group findings: category → severity-sorted [High, Medium, Low].
const FLOW_CATEGORY_ORDER = [
  "Drop-off Risk",
  "Goal Alignment",
  "CTA Clarity",
  "Consistency",
  "Missing States",
  "Progress",
  "Navigation",
  "Feedback",
  "Mobile Risk",
  "Landing Relevance",
  "Performance",
  "Accessibility",
  "Contextual",
];
const FLOW_SEVERITY_ORDER = { High: 0, Medium: 1, Low: 2 };
const FLOW_SEVERITY_COLOURS = { High: "#ff3b30", Medium: "#ff9500", Low: "#0a84ff" };

function renderFlowAnalysisIntoOverlay(payload) {
  const fa = payload.flow_analysis || {};
  const fm = payload.flow_metadata || {};

  // Header subtitle on the overlay shell — small contextual line.
  const headerCtx = [fm.client || fm.sector, fm.platform, fm.user_goal]
    .filter(Boolean).join(" · ");
  flowOverlayTitle.textContent = fm.flow_name || fa.flow_name || "Flow Analysis";
  flowOverlaySubtitle.textContent = headerCtx;

  const journeyScore = fa.journey_score;
  const scoreColour = scoreColourFor(journeyScore);
  const narrative = (fa.flow_narrative || "").trim();

  const findings = (fa.findings || []).map((f, i) => ({ ...f, _n: i + 1 }));
  const screens = collectFlowScreens();

  // Group findings by category, then severity.
  const cats = {};
  for (const f of findings) {
    const cat = f.category || "Contextual";
    (cats[cat] = cats[cat] || []).push(f);
  }
  for (const k of Object.keys(cats)) {
    cats[k].sort((a, b) =>
      (FLOW_SEVERITY_ORDER[a.severity] ?? 99) - (FLOW_SEVERITY_ORDER[b.severity] ?? 99)
    );
  }

  // Build category section HTML — empty categories are omitted entirely.
  const orderedCats = FLOW_CATEGORY_ORDER
    .concat(Object.keys(cats).filter(c => !FLOW_CATEGORY_ORDER.includes(c)));
  const catHtml = orderedCats
    .map((cat) => {
      const items = cats[cat] || [];
      if (!items.length) return "";
      return `
        <details class="fcov-cat" open>
          <summary><span>${escapeHtml(cat)}</span><span class="fcov-cat-count">${items.length}</span></summary>
          <div class="fcov-cat-body">
            ${items.map(renderFlowFindingCard).join("")}
          </div>
        </details>`;
    })
    .filter(Boolean).join("");

  // Narrative — split into paragraphs at sentence boundaries for readability.
  const narrativeHtml = narrative
    ? `<div class="fcov-narrative">${narrative
        .split(/\n{2,}/)
        .map(p => `<p>${escapeHtml(p.trim())}</p>`).join("")}</div>`
    : `<div class="fcov-empty">No narrative was produced for this analysis.</div>`;

  // Minimap — one thumb per screen, with annotation pins for any findings
  // whose `position.screen_number` matches.
  const minimapHtml = `
    <div class="fcov-minimap">
      <div class="fcov-minimap-track">
        ${screens.map((s, i) => {
          const sn = i + 1;
          const pinsForScreen = findings
            .filter(f => (f.position?.screen_number ?? 0) === sn)
            .map(f => {
              const colour = FLOW_SEVERITY_COLOURS[f.severity] || "#8a8d94";
              const x = clampPercent(f.position?.x);
              const y = clampPercent(f.position?.y);
              return `<div class="fcov-mini-pin" data-finding-n="${f._n}" style="left:${x}%;top:${y}%;background:${colour}">${f._n}</div>`;
            }).join("");
          return `
            <div class="fcov-mini" data-screen="${sn}">
              <img src="/screenshots/${escapeHtml(s.screenshot_path)}" alt="Screen ${sn}" />
              <div class="fcov-mini-label">${sn} · ${escapeHtml(s.state_name)}</div>
              ${pinsForScreen}
            </div>`;
        }).join("")}
      </div>
    </div>`;

  flowOverlayBody.innerHTML = `
    <div class="fcov-hero">
      <div class="fcov-score-card">
        <div class="fcov-score-label">Journey Score</div>
        <div class="fcov-score-num" style="color:${scoreColour}">
          ${journeyScore ?? "—"}<span class="fcov-score-suffix">/10</span>
        </div>
      </div>
      <div class="fcov-narrative-block">
        <div class="fcov-narrative-heading">Journey Overview</div>
        <div class="fcov-narrative-title">${escapeHtml(fm.flow_name || fa.flow_name || "Flow Analysis")}</div>
        ${narrativeHtml}
      </div>
    </div>

    <section class="fcov-section">
      <div class="fcov-section-head">
        <div class="fcov-section-title">Minimap</div>
        <div class="fcov-section-sub">${screens.length} screen${screens.length === 1 ? "" : "s"}</div>
      </div>
      ${minimapHtml}
    </section>

    <section class="fcov-section">
      <div class="fcov-section-head">
        <div class="fcov-section-title">Findings</div>
        <div class="fcov-section-sub">${findings.length} total</div>
      </div>
      <div class="fcov-findings">${catHtml || '<div class="fcov-empty">No findings produced for this flow.</div>'}</div>
    </section>
  `;

  // Pin click → scroll to + highlight the matching finding card.
  flowOverlayBody.querySelectorAll(".fcov-mini-pin").forEach((pin) => {
    pin.addEventListener("click", () => focusFlowFinding(parseInt(pin.dataset.findingN, 10)));
  });
  // Card click → highlight + flash matching pin.
  flowOverlayBody.querySelectorAll(".fcov-card").forEach((card) => {
    card.addEventListener("click", () => focusFlowFinding(parseInt(card.dataset.n, 10)));
  });
}

function renderFlowFindingCard(f) {
  const colour = FLOW_SEVERITY_COLOURS[f.severity] || "#8a8d94";
  const screensLabel = (f.screen_numbers && f.screen_numbers.length)
    ? "Screen " + f.screen_numbers.join(", ")
    : "";
  return `
    <div class="fcov-card" data-n="${f._n}">
      <span class="fcov-card-num" style="background:${colour}">${f._n}</span>
      <div class="fcov-card-body">
        <div class="fcov-card-text">${escapeHtml(f.finding || "")}</div>
        <div class="fcov-card-meta">
          <span class="fcov-card-sev" style="background:${colour}">${escapeHtml(f.severity || "")}</span>
          ${screensLabel ? `<span class="fcov-card-screens">${escapeHtml(screensLabel)}</span>` : ""}
        </div>
      </div>
    </div>`;
}

function focusFlowFinding(n) {
  flowOverlayBody.querySelectorAll(".fcov-card.active").forEach(c => c.classList.remove("active"));
  flowOverlayBody.querySelectorAll(".fcov-mini-pin.active").forEach(p => p.classList.remove("active"));
  const card = flowOverlayBody.querySelector(`.fcov-card[data-n="${n}"]`);
  const pin = flowOverlayBody.querySelector(`.fcov-mini-pin[data-finding-n="${n}"]`);
  if (card) {
    card.classList.add("active");
    const det = card.closest("details");
    if (det && !det.open) det.open = true;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (pin) {
    pin.classList.remove("active");
    void pin.offsetWidth;
    pin.classList.add("active");
  }
}

function clampPercent(v) {
  const n = Number(v);
  if (!isFinite(n)) return 50;
  return Math.max(0, Math.min(100, n));
}

// Legacy stubs kept so any old reference can't accidentally open a new tab.
function showFlowResultPopup(result) { openFlowOverlay(result); }
function renderFlowReportHtml() {
  // Intentionally inert — flow reports render in-canvas only.
  return "";
}

// ====================================================================
// In-canvas overlay (reusable shell)
// ====================================================================
const overlayEl = document.getElementById("canvas-overlay");
const overlayBody = document.getElementById("cov-body");
const overlayStateLabel = document.getElementById("cov-state-label");
const overlayPosition = document.getElementById("cov-position");
const overlayPrev = document.getElementById("cov-prev");
const overlayNext = document.getElementById("cov-next");
const overlayClose = document.getElementById("cov-close");

const SEVERITY_ORDER = { Critical: 0, High: 1, Medium: 2, Low: 3 };
const SEVERITY_COLOURS = {
  Critical: "#ff3b30", High: "#ff9500", Medium: "#ffcc00", Low: "#0a84ff",
};
const CATEGORY_ORDER = [
  "Functional Usability",
  "Information Architecture",
  "Visual Design",
  "Onboarding & Time-to-Value",
  "Accessibility",
];

let overlayCurrentPath = null;       // path currently shown in overlay
let overlayOrderedPaths = [];        // paths in flow order at time of opening

function openOverlayShell() {
  overlayEl.classList.remove("hidden");
  overlayEl.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}
function closeOverlay() {
  overlayEl.classList.add("hidden");
  overlayEl.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
  overlayCurrentPath = null;
}
overlayClose.addEventListener("click", closeOverlay);
overlayEl.addEventListener("click", (e) => {
  if (e.target?.dataset?.overlayClose !== undefined) closeOverlay();
});
window.addEventListener("keydown", (e) => {
  if (overlayEl.classList.contains("hidden")) return;
  if (e.key === "Escape") closeOverlay();
  else if (e.key === "ArrowLeft") overlayPrev.click();
  else if (e.key === "ArrowRight") overlayNext.click();
});

overlayPrev.addEventListener("click", () => navigateOverlayBy(-1));
overlayNext.addEventListener("click", () => navigateOverlayBy(+1));

function navigateOverlayBy(delta) {
  if (!overlayCurrentPath) return;
  const idx = overlayOrderedPaths.indexOf(overlayCurrentPath);
  if (idx < 0) return;
  const target = overlayOrderedPaths[idx + delta];
  if (!target) return;
  // Important: do NOT auto-trigger analysis. If the target hasn't been
  // analysed yet, the overlay shows its blank state and waits for the user.
  showScreenInOverlay(target);
}

/**
 * Public entry point — places `path` in the overlay.
 *  - cached/analysed: render the analysis findings + screenshot
 *  - not analysed:    render the blank-state panel (thumbnail + Analyse button)
 *
 * Never triggers analysis on its own. Fetching analysis is the user's call:
 *  - clicking the 🔍 button on a screen card (analyzeScreenshot)
 *  - clicking "Analyse Screen" on the blank-state panel
 */
function showScreenInOverlay(path) {
  if (!path) return;
  overlayOrderedPaths = collectFlowScreens().map(s => s.screenshot_path);
  overlayCurrentPath = path;

  updateOverlayHeader(path);
  openOverlayShell();

  if (analysisCache.has(path)) {
    renderScreenAnalysisIntoOverlay(path, analysisCache.get(path));
  } else if (analyzedPaths.has(path)) {
    // Server has it cached but we haven't fetched it yet — re-fetch via
    // /analyse/screen which serves the cached result instantly.
    fetchCachedAnalysis(path);
  } else {
    renderBlankStateIntoOverlay(path);
  }
}

// Backwards-compatible alias used elsewhere.
const openScreenOverlay = showScreenInOverlay;

async function fetchCachedAnalysis(path) {
  showOverlayLoading(`Loading analysis for ${labelForPath(path)}…`);
  try {
    const result = await runScreenAnalysis(path);
    analysisCache.set(path, result);
    if (overlayCurrentPath === path) {
      renderScreenAnalysisIntoOverlay(path, result);
    }
  } catch (err) {
    console.error("[Overlay] cache fetch failed:", err);
    showOverlayError(err.message || "Could not load cached analysis.");
  }
}

/**
 * Blank state inside the overlay — shown when the user navigates to a
 * screen that hasn't been analysed yet. Never auto-triggers analysis.
 */
function renderBlankStateIntoOverlay(path) {
  const screenshotUrl = "/screenshots/" + path;
  overlayBody.innerHTML = `
    <div class="cov-blank">
      <div class="cov-blank-card">
        <div class="cov-blank-thumb">
          <img src="${screenshotUrl}" alt="Screenshot preview" />
        </div>
        <div class="cov-blank-text">This screen has not been analysed yet.</div>
        <button class="cov-blank-btn" id="cov-blank-analyse">Analyse Screen</button>
      </div>
    </div>`;
  const btn = overlayBody.querySelector("#cov-blank-analyse");
  if (btn) btn.addEventListener("click", () => analyseFromBlankState(path));
}

/**
 * Triggered by the blank-state "Analyse Screen" button. Opens the loader on
 * top of the overlay (loader z-index > overlay z-index), runs the pipeline,
 * then replaces the blank state with the rendered findings — overlay stays
 * open the whole time.
 */
async function analyseFromBlankState(path) {
  if (!SESSION_ID) return;
  openLoader({
    title: "Analysing screen…",
    subtitle: labelForPath(path),
    includeFlow: false,
  });
  subscribeProgress(SESSION_ID);
  try {
    const result = await runScreenAnalysis(path);
    analysisCache.set(path, result);
    analyzedPaths.add(path);
    screenEls[path]?.el.classList.add("analyzed");
    closeLoader();
    if (overlayCurrentPath === path) {
      renderScreenAnalysisIntoOverlay(path, result);
    }
  } catch (err) {
    console.error("[BlankAnalyse]", err);
    loaderTitleEl.textContent = "Analysis failed";
    loaderSubtitleEl.textContent = err.message || "Something went wrong.";
  }
}

function updateOverlayHeader(path) {
  overlayStateLabel.textContent = labelForPath(path);
  const idx = overlayOrderedPaths.indexOf(path);
  const total = overlayOrderedPaths.length;
  overlayPosition.textContent = idx >= 0
    ? `Screen ${idx + 1} of ${total}`
    : "";
  overlayPrev.disabled = idx <= 0;
  overlayNext.disabled = idx < 0 || idx >= total - 1;
}

function showOverlayLoading(text) {
  overlayBody.innerHTML = `
    <div class="cov-loading">
      <div class="cov-spinner"></div>
      <div class="cov-loading-text">${escapeHtml(text)}</div>
      <div class="cov-loading-sub">Running 5 specialists in parallel…</div>
    </div>`;
}
function showOverlayError(text) {
  overlayBody.innerHTML = `
    <div class="cov-loading">
      <div class="cov-loading-text">Analysis failed</div>
      <div class="cov-loading-sub">${escapeHtml(text)}</div>
    </div>`;
}

// Render the per-screen result into the overlay body (sidebar + image+pins).
function renderScreenAnalysisIntoOverlay(path, result) {
  const findings = orderFindings(result.findings || []);
  const screenshotUrl = "/screenshots/" + path;
  const score = result.overall_score;
  const scoreColour = scoreColourFor(score);

  // Build sidebar HTML
  const cats = groupByCategory(findings);
  const catHtml = CATEGORY_ORDER
    .concat(Object.keys(cats).filter(c => !CATEGORY_ORDER.includes(c)))
    .map((cat) => {
      const items = cats[cat] || [];
      if (!items.length) return "";
      return `
        <details class="cov-cat" open>
          <summary><span>${escapeHtml(cat)}</span><span class="cov-cat-count">${items.length}</span></summary>
          <div class="cov-cat-body">
            ${items.map(f => renderFindingCard(f)).join("")}
          </div>
        </details>`;
    })
    .filter(Boolean).join("");

  const topFinding = result.top_priority_finding ? `
    <div class="cov-top-finding">
      <div class="cov-top-label">Top Priority</div>
      ${escapeHtml(result.top_priority_finding)}
    </div>` : "";

  overlayBody.innerHTML = `
    <aside class="cov-sidebar">
      <header class="cov-sidebar-header">
        <div class="cov-score-row">
          <div class="cov-score-num" style="color:${scoreColour}">${score ?? "—"}<span class="cov-score-suffix">/10</span></div>
          <div class="cov-score-meta">
            <div class="cov-score-label">Overall UI Score</div>
            <div class="cov-pills">
              <span class="cov-pill">${findings.length} findings</span>
              <span class="cov-pill">${result.critical_issues_count || 0} critical</span>
            </div>
          </div>
        </div>
        <div class="cov-rationale">${escapeHtml(result.score_rationale || "")}</div>
        ${topFinding}
      </header>
      <div class="cov-findings">${catHtml || '<div class="cov-rationale">No findings.</div>'}</div>
    </aside>
    <section class="cov-canvas">
      <div class="cov-canvas-pad">
        <div class="cov-image-wrap">
          <div class="cov-screenshot-frame">
            <img id="cov-screenshot" src="${screenshotUrl}" alt="Screenshot under analysis" />
            <div class="cov-pin-layer" id="cov-pin-layer"></div>
          </div>
        </div>
      </div>
    </section>
  `;

  // Wire up sidebar ↔ pin focus.
  const pinLayer = overlayBody.querySelector("#cov-pin-layer");
  const img = overlayBody.querySelector("#cov-screenshot");

  const renderPins = () => {
    pinLayer.innerHTML = "";
    findings.forEach((f) => {
      if (!f.position) return;
      const pin = document.createElement("div");
      pin.className = "cov-pin";
      pin.dataset.n = f._n;
      pin.style.left = f.position.x + "%";
      pin.style.top = f.position.y + "%";
      pin.style.background = SEVERITY_COLOURS[f.severity] || "#8a8d94";
      pin.textContent = f._n;
      pin.addEventListener("click", () => focusOverlayFinding(f._n));
      pinLayer.appendChild(pin);
    });
  };
  if (img.complete) renderPins();
  else img.addEventListener("load", renderPins);

  overlayBody.querySelectorAll(".cov-card").forEach((card) => {
    card.addEventListener("click", () => focusOverlayFinding(parseInt(card.dataset.n, 10)));
  });
}

function focusOverlayFinding(n) {
  overlayBody.querySelectorAll(".cov-card.active").forEach(c => c.classList.remove("active"));
  overlayBody.querySelectorAll(".cov-pin.active").forEach(p => p.classList.remove("active"));

  const card = overlayBody.querySelector(`.cov-card[data-n="${n}"]`);
  const pin = overlayBody.querySelector(`.cov-pin[data-n="${n}"]`);
  if (card) {
    card.classList.add("active");
    const det = card.closest("details");
    if (det && !det.open) det.open = true;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  if (pin) {
    pin.classList.remove("active");
    void pin.offsetWidth; // restart animation
    pin.classList.add("active");
  }
}

function renderFindingCard(f) {
  const sev = f.severity || "Medium";
  const sevColour = SEVERITY_COLOURS[sev] || "#8a8d94";
  const polarity = f.polarity || "negative";
  const polColour = polarity === "positive" ? "#34c759" : "#ff3b30";
  return `
    <div class="cov-card" data-n="${f._n}">
      <span class="cov-num" style="background:${sevColour}">${f._n}</span>
      <div class="cov-card-body">
        <div class="cov-element">${escapeHtml(f.element || "")}</div>
        <div class="cov-finding">${escapeHtml(f.finding || "")}</div>
        <div class="cov-meta">
          <span class="cov-sev" style="background:${sevColour}">${escapeHtml(sev)}</span>
          <span class="cov-pol-dot" style="background:${polColour}"></span>
          <span class="cov-pol-label">${escapeHtml(polarity)}</span>
        </div>
      </div>
    </div>`;
}

function orderFindings(findings) {
  const byCat = {};
  for (const f of findings) {
    const cat = f.category || "Uncategorised";
    (byCat[cat] = byCat[cat] || []).push(f);
  }
  const ordered = [];
  for (const cat of CATEGORY_ORDER.concat(Object.keys(byCat).filter(c => !CATEGORY_ORDER.includes(c)))) {
    const items = (byCat[cat] || []).slice().sort((a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99)
    );
    ordered.push(...items);
  }
  ordered.forEach((f, i) => { f._n = i + 1; });
  return ordered;
}

function groupByCategory(findings) {
  const out = {};
  for (const f of findings) {
    const c = f.category || "Uncategorised";
    (out[c] = out[c] || []).push(f);
  }
  return out;
}

function scoreColourFor(score) {
  if (score == null) return "#8a8d94";
  if (score < 4) return "#ff3b30";
  if (score <= 6) return "#ff9500";
  return "#34c759";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ====================================================================
// Init
// ====================================================================
restoreLayout();
pollSession();
setInterval(pollSession, POLL_INTERVAL_MS);
window.addEventListener("resize", drawConnections);

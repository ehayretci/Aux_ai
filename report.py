"""
Report Generator
================

Takes the JSON output of `evaluate.py` and produces a single standalone
`report.html` file (no external CSS/JS, screenshot embedded as a data URL).

Layout:
  Left  (35%) — findings sidebar grouped by category, sorted by severity
  Right (65%) — annotated screenshot with numbered circles at the AI's
                estimated x/y positions

Annotation numbers are shared across the two panels: card #3 in the sidebar
corresponds to circle #3 on the image.

Run directly to evaluate the first screenshot under `screenshots/` and emit
`report.html` next to it.
"""

import os
import sys
import json
import html
import base64
from pathlib import Path


# ---------------------------------------------------------------- constants --
# Display order — must match the `category` strings produced by the specialists.
CATEGORY_ORDER = [
    "Functional Usability",
    "Information Architecture",
    "Visual Design",
    "Onboarding & Time-to-Value",
    "Accessibility",
]

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

SEVERITY_COLOURS = {
    "Critical": "#ff3b30",   # red
    "High":     "#ff9500",   # orange
    "Medium":   "#ffcc00",   # yellow
    "Low":      "#0a84ff",   # blue
}


# ---------------------------------------------------------------- helpers ----
def _score_colour(score):
    if score is None:
        return "#8a8d94"
    try:
        s = int(score)
    except (TypeError, ValueError):
        return "#8a8d94"
    if s < 4:
        return "#ff3b30"
    if s <= 6:
        return "#ff9500"
    return "#34c759"


def _embed_image(image_path: str) -> str:
    """Read the screenshot from disk and return a base64 data: URL."""
    ext = Path(image_path).suffix.lstrip(".").lower() or "png"
    if ext == "jpg":
        ext = "jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/{ext};base64,{b64}"


def _order_findings(findings: list) -> list:
    """Sort by category order then severity, assigning each a stable `_n`.

    The input list is mutated to add the `_n` index used for sidebar ↔ pin
    cross-referencing.
    """
    by_cat: dict = {c: [] for c in CATEGORY_ORDER}
    for f in findings:
        cat = f.get("category", "Uncategorised")
        by_cat.setdefault(cat, []).append(f)

    ordered: list = []
    seen_cats = set()
    for cat in CATEGORY_ORDER:
        items = by_cat.get(cat, [])
        items.sort(key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 99))
        ordered.extend(items)
        seen_cats.add(cat)
    # Append any unexpected categories at the end so nothing is lost.
    for cat, items in by_cat.items():
        if cat in seen_cats:
            continue
        items.sort(key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 99))
        ordered.extend(items)

    for i, f in enumerate(ordered, start=1):
        f["_n"] = i
    return ordered


# ---------------------------------------------------------------- builder ----
def build_report(
    result: dict,
    output_path: str = "report.html",
    state_label: str = "",
    flow_position: str = "",
    prev_url: str = "",
    next_url: str = "",
) -> str:
    """Render `result` (an `evaluate()` output dict) into a standalone HTML file.

    Optional args:
      state_label   "State 2 — Screen 3" — shown above the verdict.
      flow_position "Screen 3 of 8"      — shown between the nav arrows.
      prev_url      URL of the previous screen's report (or empty).
      next_url      URL of the next screen's report (or empty).
    """
    findings = _order_findings(list(result.get("findings", [])))
    image_data_url = _embed_image(result["image_path"])

    score = result.get("overall_score")
    score_colour = _score_colour(score)
    rationale = result.get("score_rationale", "")
    critical_count = result.get("critical_issues_count", 0)
    top_priority = result.get("top_priority_finding", "")

    # Pre-build sidebar HTML (server-rendered so it prints correctly even if JS off).
    cats_html_parts = []
    for cat in CATEGORY_ORDER + [c for c in {f["category"] for f in findings if f.get("category") not in CATEGORY_ORDER}]:
        items = [f for f in findings if f.get("category") == cat]
        if not items:
            continue
        cards = []
        for f in items:
            sev = f.get("severity", "Medium")
            sev_colour = SEVERITY_COLOURS.get(sev, "#8a8d94")
            polarity = f.get("polarity", "negative")
            polarity_colour = "#34c759" if polarity == "positive" else "#ff3b30"
            cards.append(f"""
              <div class="finding-card" data-n="{f['_n']}" data-severity="{html.escape(sev)}">
                <div class="finding-row">
                  <span class="num-circle" style="background:{sev_colour}">{f['_n']}</span>
                  <div class="finding-body">
                    <div class="finding-element">{html.escape(f.get('element', ''))}</div>
                    <div class="finding-text">{html.escape(f.get('finding', ''))}</div>
                    <div class="finding-meta">
                      <span class="sev-badge" style="background:{sev_colour}">{html.escape(sev)}</span>
                      <span class="polarity-dot" style="background:{polarity_colour}" title="{html.escape(polarity)}"></span>
                      <span class="polarity-label">{html.escape(polarity)}</span>
                    </div>
                  </div>
                </div>
              </div>
            """)
        cats_html_parts.append(f"""
          <details class="cat-section" open>
            <summary class="cat-header">
              <span class="cat-name">{html.escape(cat)}</span>
              <span class="cat-count">{len(items)}</span>
            </summary>
            <div class="cat-body">
              {''.join(cards)}
            </div>
          </details>
        """)

    sidebar_inner = "\n".join(cats_html_parts) or '<div class="empty">No findings.</div>'

    # JSON payload for the JS to render annotation pins.
    findings_payload = json.dumps([
        {
            "n": f["_n"],
            "x": float(f["position"]["x"]),
            "y": float(f["position"]["y"]),
            "element": f.get("element", ""),
            "severity": f.get("severity", "Medium"),
            "colour": SEVERITY_COLOURS.get(f.get("severity"), "#8a8d94"),
        }
        for f in findings
    ])

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>UI Analysis Report</title>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0;
    height: 100%;
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
    background: #0e0f12;
    color: #e6e7ea;
    overflow: hidden;
  }}

  .layout {{ display: flex; height: 100vh; width: 100vw; }}

  /* ---------- LEFT SIDEBAR ---------- */
  .sidebar {{
    width: 35%;
    min-width: 360px;
    max-width: 560px;
    border-right: 1px solid #1d1f24;
    background: #14161a;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}
  .sidebar-header {{
    padding: 24px;
    border-bottom: 1px solid #1d1f24;
  }}
  .score-row {{ display: flex; align-items: center; gap: 16px; }}
  .score-number {{
    font-size: 56px;
    font-weight: 700;
    line-height: 1;
    color: {score_colour};
  }}
  .score-meta {{ flex: 1; }}
  .score-label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #8a8d94;
    margin-bottom: 4px;
  }}
  .score-suffix {{ font-size: 16px; color: #6a6d74; margin-left: 4px; }}
  .score-rationale {{
    margin-top: 14px;
    font-size: 13px;
    line-height: 1.5;
    color: #c0c2c7;
  }}
  .topline-row {{
    margin-top: 14px;
    display: flex;
    gap: 12px;
    font-size: 12px;
    color: #8a8d94;
  }}
  .topline-row .pill {{
    background: #1d1f24;
    border: 1px solid #2a2d34;
    border-radius: 999px;
    padding: 4px 10px;
  }}

  .top-finding {{
    margin: 16px 24px 0;
    padding: 12px 14px;
    background: #1a1c21;
    border-left: 3px solid #ff3b30;
    border-radius: 6px;
    font-size: 12px;
    color: #d4d6da;
    line-height: 1.5;
  }}
  .top-finding-label {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #ff3b30;
    margin-bottom: 4px;
    font-weight: 700;
  }}

  .findings-scroll {{
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px 80px;
  }}
  .cat-section {{
    margin-bottom: 8px;
    border: 1px solid #1d1f24;
    border-radius: 8px;
    background: #16181d;
    overflow: hidden;
  }}
  .cat-section[open] {{ background: #16181d; }}
  .cat-header {{
    list-style: none;
    cursor: pointer;
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 14px;
    font-weight: 600;
    font-size: 13px;
    color: #e6e7ea;
    user-select: none;
  }}
  .cat-header::-webkit-details-marker {{ display: none; }}
  .cat-header::before {{
    content: "▸";
    margin-right: 8px;
    color: #6a6d74;
    transition: transform 0.15s;
    display: inline-block;
  }}
  .cat-section[open] > .cat-header::before {{ transform: rotate(90deg); }}
  .cat-count {{
    background: #2a2d34;
    color: #c0c2c7;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
  }}
  .cat-body {{ padding: 4px 8px 10px; }}

  .finding-card {{
    padding: 10px 8px;
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.12s;
  }}
  .finding-card:hover {{ background: #1d1f24; }}
  .finding-card.active {{
    background: #20232a;
    box-shadow: inset 0 0 0 1px #3a3d44;
  }}
  .finding-row {{ display: flex; gap: 10px; align-items: flex-start; }}
  .num-circle {{
    flex: 0 0 auto;
    width: 24px; height: 24px;
    border-radius: 50%;
    color: white;
    font-size: 11px;
    font-weight: 700;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 0 0 2px #14161a;
  }}
  .finding-body {{ flex: 1; min-width: 0; }}
  .finding-element {{
    font-size: 12px;
    font-weight: 700;
    color: #e6e7ea;
    margin-bottom: 4px;
  }}
  .finding-text {{
    font-size: 12px;
    line-height: 1.45;
    color: #b0b3b8;
    margin-bottom: 6px;
  }}
  .finding-meta {{ display: flex; align-items: center; gap: 8px; }}
  .sev-badge {{
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: white;
  }}
  .polarity-dot {{
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
  }}
  .polarity-label {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: #8a8d94;
  }}

  .sidebar-footer {{
    padding: 14px 16px;
    border-top: 1px solid #1d1f24;
    background: #14161a;
  }}
  .export-btn {{
    width: 100%;
    background: #0a84ff;
    color: white;
    border: none;
    padding: 10px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }}
  .export-btn:hover {{ background: #0a74e0; }}

  /* ---------- TOP NAV (state label + arrow nav) ---------- */
  .top-nav {{
    position: absolute;
    top: 16px; left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 12px;
    background: rgba(20, 22, 26, 0.92);
    backdrop-filter: blur(8px);
    border: 1px solid #1d1f24;
    border-radius: 999px;
    padding: 6px 8px;
    z-index: 30;
  }}
  .top-nav .state-label {{
    font-size: 12px;
    font-weight: 700;
    color: #e6e7ea;
    padding: 0 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }}
  .top-nav button.nav-arrow {{
    background: #1d1f24;
    color: #e6e7ea;
    border: 1px solid #2a2d34;
    border-radius: 50%;
    width: 30px; height: 30px;
    font-size: 14px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }}
  .top-nav button.nav-arrow:hover {{ background: #2a2d34; }}
  .top-nav button.nav-arrow:disabled {{
    opacity: 0.35; cursor: not-allowed;
  }}
  .top-nav .position-text {{
    font-size: 11px;
    color: #8a8d94;
    min-width: 80px;
    text-align: center;
  }}

  /* ---------- RIGHT PANEL ---------- */
  .canvas {{
    flex: 1;
    position: relative;
    background:
      radial-gradient(circle, #1d1f24 1px, transparent 1px) 0 0 / 32px 32px,
      #0e0f12;
    overflow: hidden;
  }}
  .canvas-pad {{
    position: absolute;
    top: 12%; left: 12%;
    right: 12%; bottom: 12%;
  }}
  .image-wrap {{
    position: relative;
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .screenshot-frame {{
    position: relative;
    max-width: 100%;
    max-height: 100%;
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.6);
    border-radius: 10px;
    overflow: hidden;
    background: #000;
  }}
  .screenshot-frame img {{
    display: block;
    max-width: 100%;
    max-height: 100%;
    width: auto;
    height: auto;
  }}
  #pin-layer {{
    position: absolute;
    top: 0; left: 0;
    width: 100%; height: 100%;
    pointer-events: none;
  }}
  .pin {{
    position: absolute;
    transform: translate(-50%, -50%);
    width: 28px; height: 28px;
    border-radius: 50%;
    color: white;
    font-size: 12px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.95),
                0 4px 14px rgba(0, 0, 0, 0.5);
    pointer-events: auto;
    transition: transform 0.15s ease;
    z-index: 1;
  }}
  .pin:hover {{ transform: translate(-50%, -50%) scale(1.18); z-index: 5; }}
  .pin.active {{
    animation: pulse 1s ease 2;
    z-index: 10;
  }}
  @keyframes pulse {{
    0%   {{ box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.95), 0 0 0 0 rgba(255, 255, 255, 0.7); transform: translate(-50%, -50%) scale(1); }}
    50%  {{ box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.95), 0 0 0 18px rgba(255, 255, 255, 0); transform: translate(-50%, -50%) scale(1.3); }}
    100% {{ box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.95), 0 0 0 0 rgba(255, 255, 255, 0); transform: translate(-50%, -50%) scale(1); }}
  }}
  .pin .tip {{
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    background: #14161a;
    color: #e6e7ea;
    border: 1px solid #2a2d34;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 500;
    white-space: nowrap;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.15s;
  }}
  .pin:hover .tip {{ opacity: 1; }}

  .empty {{ padding: 24px; color: #8a8d94; font-size: 13px; }}

  /* ---------- PRINT ---------- */
  @media print {{
    @page {{ size: landscape; margin: 12mm; }}
    html, body {{
      background: white !important;
      color: #111 !important;
      overflow: visible !important;
      height: auto !important;
    }}
    .layout {{ height: auto !important; }}
    .sidebar {{
      width: 38% !important;
      min-width: 0 !important;
      max-width: none !important;
      background: white !important;
      color: #111 !important;
      border-right: 1px solid #ddd !important;
      overflow: visible !important;
    }}
    .sidebar-header {{ border-color: #ddd !important; }}
    .findings-scroll {{ overflow: visible !important; padding-bottom: 0 !important; }}
    .cat-section {{
      background: white !important;
      border-color: #ddd !important;
      page-break-inside: avoid;
    }}
    .cat-header, .cat-name {{ color: #111 !important; }}
    .finding-card {{ page-break-inside: avoid; }}
    .finding-element {{ color: #111 !important; }}
    .finding-text {{ color: #333 !important; }}
    .canvas {{
      background: white !important;
    }}
    .canvas-pad {{ top: 4%; left: 4%; right: 4%; bottom: 4%; }}
    .sidebar-footer, .export-btn {{ display: none !important; }}
    details[open] > .cat-header::before {{ color: #111 !important; }}
    .top-finding {{ background: #fff5f4 !important; color: #111 !important; }}
    .num-circle {{ box-shadow: none !important; }}
  }}
</style>
</head>
<body>
  <div class="layout">
    <!-- LEFT: SIDEBAR -->
    <aside class="sidebar">
      <header class="sidebar-header">
        <div class="score-row">
          <div class="score-number">{html.escape(str(score) if score is not None else "—")}<span class="score-suffix">/10</span></div>
          <div class="score-meta">
            <div class="score-label">Overall UI Score</div>
            <div class="topline-row">
              <span class="pill">{len(findings)} findings</span>
              <span class="pill">{int(critical_count or 0)} critical</span>
            </div>
          </div>
        </div>
        <div class="score-rationale">{html.escape(rationale)}</div>
        {f'<div class="top-finding"><div class="top-finding-label">Top Priority</div>{html.escape(top_priority)}</div>' if top_priority else ''}
      </header>

      <div class="findings-scroll" id="findings-scroll">
        {sidebar_inner}
      </div>

      <footer class="sidebar-footer">
        <button class="export-btn" onclick="window.print()">Export PDF</button>
      </footer>
    </aside>

    <!-- RIGHT: ANNOTATED IMAGE -->
    <section class="canvas">
      <nav class="top-nav" aria-label="Screen navigation">
        <button class="nav-arrow" id="prev-btn" aria-label="Previous screen"
                {("disabled" if not prev_url else "")}
                {(f'onclick="location.href={json.dumps(prev_url)}"' if prev_url else "")}>
          ‹
        </button>
        <span class="state-label">{html.escape(state_label) if state_label else ""}</span>
        <span class="position-text">{html.escape(flow_position) if flow_position else ""}</span>
        <button class="nav-arrow" id="next-btn" aria-label="Next screen"
                {("disabled" if not next_url else "")}
                {(f'onclick="location.href={json.dumps(next_url)}"' if next_url else "")}>
          ›
        </button>
      </nav>

      <div class="canvas-pad">
        <div class="image-wrap">
          <div class="screenshot-frame" id="screenshot-frame">
            <img id="screenshot" alt="Screenshot under analysis" src="{image_data_url}" />
            <div id="pin-layer"></div>
          </div>
        </div>
      </div>
    </section>
  </div>

<script>
  const FINDINGS = {findings_payload};

  const pinLayer = document.getElementById("pin-layer");
  const screenshot = document.getElementById("screenshot");

  function renderPins() {{
    pinLayer.innerHTML = "";
    FINDINGS.forEach(f => {{
      const pin = document.createElement("div");
      pin.className = "pin";
      pin.dataset.n = f.n;
      pin.style.left = f.x + "%";
      pin.style.top  = f.y + "%";
      pin.style.background = f.colour;
      pin.textContent = f.n;
      const tip = document.createElement("span");
      tip.className = "tip";
      tip.textContent = f.element;
      pin.appendChild(tip);
      pin.addEventListener("click", () => focusFinding(f.n, true));
      pinLayer.appendChild(pin);
    }});
  }}

  function focusFinding(n, scrollSidebar) {{
    document.querySelectorAll(".finding-card.active").forEach(c => c.classList.remove("active"));
    document.querySelectorAll(".pin.active").forEach(p => p.classList.remove("active"));

    const card = document.querySelector(`.finding-card[data-n="${{n}}"]`);
    const pin  = document.querySelector(`.pin[data-n="${{n}}"]`);
    if (card) {{
      card.classList.add("active");
      // Open the parent <details> if it was collapsed.
      const details = card.closest("details");
      if (details && !details.open) details.open = true;
      if (scrollSidebar) {{
        card.scrollIntoView({{ behavior: "smooth", block: "center" }});
      }}
    }}
    if (pin) {{
      pin.classList.remove("active"); // restart animation
      void pin.offsetWidth;
      pin.classList.add("active");
    }}
  }}

  // Bind sidebar cards once the DOM is parsed.
  document.querySelectorAll(".finding-card").forEach(card => {{
    card.addEventListener("click", () => focusFinding(parseInt(card.dataset.n, 10), false));
  }});

  if (screenshot.complete) renderPins();
  else screenshot.addEventListener("load", renderPins);
  window.addEventListener("resize", renderPins);
</script>
</body>
</html>
"""

    out = Path(output_path)
    out.write_text(html_doc, encoding="utf-8")
    return str(out.resolve())


# ---------------------------------------------------------------- main / test
def _first_screenshot(root: str = "screenshots") -> str:
    """Walk `screenshots/` and return the first PNG/JPG path found."""
    if not os.path.isdir(root):
        raise FileNotFoundError(f"No `{root}` directory in cwd.")
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.lower().endswith((".png", ".jpg", ".jpeg")):
                return os.path.join(dirpath, name)
    raise FileNotFoundError(f"No screenshots found inside `{root}`.")


if __name__ == "__main__":
    from evaluate import evaluate

    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        image_path = _first_screenshot()
        print(f"[report] using first screenshot: {image_path}")

    print("[report] evaluating…")
    result = evaluate(image_path)

    output = sys.argv[2] if len(sys.argv) > 2 else "report.html"
    print(f"[report] writing {output}…")
    final_path = build_report(result, output)
    print(f"[report] done → {final_path}")

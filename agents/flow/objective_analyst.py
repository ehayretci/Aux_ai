"""
Objective Flow Analyst
======================

Looks at every screenshot in the user's flow, in order, plus the per-screen
specialist evaluations (when available). Evaluates the journey against
objective UX heuristics that don't depend on the brand or business context:

  - Step count vs. sector benchmark
  - Cross-screen consistency (labels, buttons, terminology, visual rhythm)
  - Drop-off risk points (sudden complexity / cognitive-load jumps)
  - Missing states (loading, empty, error, confirmation, back-nav)
  - CTA clarity at every step
  - Progress indicators when multi-step
  - Looping behaviour (does the user end up where they started?)
  - Shortcuts (skip-link / autofill / smart defaults)
  - Reward & feedback after key actions
  - Accessibility considered at the journey level (contrast, target sizes,
    keyboard reachability across the whole sequence)

Output is a flat findings list. Each finding is tagged with one of the
canonical flow categories (see CATEGORIES below). The synthesiser later
merges these with the contextual analyst's findings, dedupes, and produces
the final report.
"""

import os
import json
from typing import Dict, List, Optional

import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

MODEL = "gemini-2.5-flash"

# Canonical category labels the objective analyst is allowed to use.
CATEGORIES = [
    "Consistency",
    "Drop-off Risk",
    "Missing States",
    "CTA Clarity",
    "Progress",
    "Navigation",
    "Feedback",
    "Accessibility",
]


SYSTEM_PROMPT_TEMPLATE = """You are a senior UX strategist running OBJECTIVE journey-level analysis.

You are given:
  - A sequence of screenshots in the order the user encounters them.
  - For each: a state name and a screen number (1-indexed).
  - (Optionally) the per-screen specialist findings produced earlier in the pipeline.
  - The flow metadata (flow name, client/sector, platform, target user, user goal).

Flow context:
  - Flow name : {flow_name}
  - Client    : {client}
  - Platform  : {platform}
  - Target    : {target_user}
  - Goal      : {user_goal}

Evaluate the COMPLETE journey across these objective dimensions. For EACH
dimension, produce zero or more findings (only emit findings that are real
— never invent ones to fill a slot):

1. CONSISTENCY      — labels, button styles, terminology, layout patterns
                      across screens. Inconsistencies between specific screens.
2. DROP-OFF RISK    — points where complexity, required input or cognitive
                      load jumps relative to the previous screen.
3. MISSING STATES   — loading, empty, error, confirmation, back-navigation
                      that should plausibly exist in a flow of this type.
4. CTA CLARITY      — primary action visibility, label specificity, hierarchy
                      vs. secondary actions on every screen.
5. PROGRESS         — multi-step indicators, where-am-I cues, ability to
                      preview remaining work.
6. NAVIGATION       — looping behaviour, shortcuts, skip-links, autofill
                      hints, smart defaults across the journey.
7. FEEDBACK         — reward/confirmation after key actions, success states,
                      microcopy that acknowledges progress.
8. ACCESSIBILITY    — at journey level: contrast across screens, touch
                      targets that vary in size, keyboard reachability,
                      visible focus across the sequence.

For each finding:
  - `category`       — EXACTLY one of: Consistency, Drop-off Risk, Missing States,
                       CTA Clarity, Progress, Navigation, Feedback, Accessibility.
  - `severity`       — High | Medium | Low. (Critical is reserved for screen
                       analysis; flow-level uses 3 tiers.)
  - `screen_numbers` — array of 1-indexed screen numbers the finding refers to.
                       For cross-screen issues, list every relevant screen.
  - `position`       — {{ "screen_number": <int>, "x": <0-100>, "y": <0-100> }}.
                       Pick the SINGLE most representative screen for the
                       finding's annotation pin and estimate where on that
                       screen the issue sits.
  - `finding`        — one neutral, factual sentence describing what you see.

Also produce journey_metrics:
  - total_steps           — number of distinct screens in the flow
  - benchmark_steps       — your estimate of the typical step count for this
                            flow type in this sector
  - benchmark_comparison  — "above" | "on-par" | "below"

Return JSON matching this exact schema:
{{
  "findings": [
    {{
      "category": "...",
      "severity": "High" | "Medium" | "Low",
      "screen_numbers": [<int>, ...],
      "position": {{ "screen_number": <int>, "x": <float>, "y": <float> }},
      "finding": "<one neutral sentence>"
    }}
  ],
  "journey_metrics": {{
    "total_steps": <int>,
    "benchmark_steps": <int>,
    "benchmark_comparison": "above" | "on-par" | "below"
  }}
}}

Quality bar:
  - Every finding must be specific (name the screen number(s), the element).
  - No invented findings. If a dimension has nothing real to flag, emit none.
  - Severity calibration: High = will cost conversions; Medium = noticeable
    friction; Low = polish-level."""


def _build_system_prompt(flow_metadata: Dict) -> str:
    fm = flow_metadata or {}
    return SYSTEM_PROMPT_TEMPLATE.format(
        flow_name=fm.get("flow_name") or "Untitled Flow",
        client=fm.get("client") or fm.get("sector") or "Unspecified",
        platform=fm.get("platform") or "Unspecified",
        target_user=fm.get("target_user") or "Unspecified",
        user_goal=fm.get("user_goal") or "Unspecified",
    )


def run(
    screens: List[Dict],
    screen_evaluations: Optional[List[Dict]] = None,
    flow_metadata: Optional[Dict] = None,
) -> Dict:
    """Run the objective analyst.

    Args:
        screens: ordered list of {"image_path", "state_name", "screen_number"}.
        screen_evaluations: optional per-screen evaluation outputs aligned with `screens`.
        flow_metadata: flow-level metadata dict.

    Returns:
        {"findings": [...], "journey_metrics": {...}}
    """
    if not screens:
        return {"findings": [], "journey_metrics": {}}

    flow_metadata = flow_metadata or {}
    system_prompt = _build_system_prompt(flow_metadata)

    images = []
    journey_lines = []
    for i, s in enumerate(screens, start=1):
        path = s.get("image_path")
        if not path or not os.path.exists(path):
            continue
        try:
            images.append(Image.open(path))
        except Exception:
            continue
        journey_lines.append(
            f"  {i}. State: {s.get('state_name', '?')} — Screen {s.get('screen_number', i)}"
        )

    if not images:
        return {"findings": [], "journey_metrics": {}}

    parts = ["FLOW JOURNEY (in order):\n" + "\n".join(journey_lines)]
    if screen_evaluations:
        compact = []
        for i, ev in enumerate(screen_evaluations):
            if not ev:
                continue
            compact.append({
                "screen_number": (screens[i] or {}).get("screen_number", i + 1),
                "state_name": (screens[i] or {}).get("state_name"),
                "overall_score": ev.get("overall_score"),
                "score_rationale": ev.get("score_rationale"),
                "top_priority_finding": ev.get("top_priority_finding"),
                "findings": (ev.get("findings") or [])[:6],
            })
        parts.append("PER-SCREEN SPECIALIST FINDINGS:\n" + json.dumps(compact, indent=2))
    parts.append("Produce the OBJECTIVE flow analysis JSON now.")

    contents = [parts[0]] + images + parts[1:]

    try:
        model = genai.GenerativeModel(
            MODEL,
            system_instruction=system_prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        response = model.generate_content(contents)
        out = json.loads(response.text)
    except Exception as e:
        print(f"[objective_analyst] error: {e}")
        return {"findings": [], "journey_metrics": {}, "error": str(e)}

    # Defensive shape-fixup.
    findings = out.get("findings") or []
    cleaned = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        f.setdefault("category", "Consistency")
        if f["category"] not in CATEGORIES:
            f["category"] = "Consistency"
        f.setdefault("severity", "Medium")
        if f["severity"] not in ("High", "Medium", "Low"):
            f["severity"] = "Medium"
        f.setdefault("screen_numbers", [])
        if not isinstance(f["screen_numbers"], list):
            f["screen_numbers"] = []
        pos = f.get("position") or {}
        if not isinstance(pos, dict):
            pos = {}
        pos.setdefault("screen_number", (f["screen_numbers"][0] if f["screen_numbers"] else 1))
        try:
            pos["x"] = float(pos.get("x", 50))
            pos["y"] = float(pos.get("y", 50))
        except (TypeError, ValueError):
            pos["x"], pos["y"] = 50.0, 50.0
        f["position"] = pos
        f.setdefault("finding", "")
        cleaned.append(f)
    out["findings"] = cleaned
    out.setdefault("journey_metrics", {})
    return out

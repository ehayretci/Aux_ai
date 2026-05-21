"""
Flow Synthesiser
================

Receives findings from the objective and contextual analysts plus the flow
metadata, then produces:

  1. A journey_score (1-10) — the SINGLE number that appears in the report.
  2. A flow_narrative — 4-8 sentences of expert prose describing the user's
     experience through this specific flow. Specific, contextual, never
     generic. References the client/user/goal by name when relevant.
  3. A curated findings list — deduped across the two analysts, organised
     by category, with severity High/Medium/Low only (no Critical at flow
     level).

Each finding carries `screen_numbers` and `position` so the frontend can
render annotation pins on the journey minimap.
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

# Final report categories (union of both analysts).
CATEGORIES = [
    "Consistency",
    "Drop-off Risk",
    "Missing States",
    "CTA Clarity",
    "Progress",
    "Navigation",
    "Feedback",
    "Accessibility",
    "Goal Alignment",
    "Mobile Risk",
    "Landing Relevance",
    "Performance",
    "Contextual",
]


SYSTEM_PROMPT_TEMPLATE = """You are a senior UX lead authoring the FINAL flow analysis report.

You receive two findings lists:
  - OBJECTIVE findings: from the universal UX heuristics analyst.
  - CONTEXTUAL findings: from the user/client/business context analyst.

Plus the flow metadata:
  - Flow name : {flow_name}
  - Client    : {client}
  - Platform  : {platform}
  - Target    : {target_user}
  - Goal      : {user_goal}

Your job has THREE parts:

PART 1 — JOURNEY SCORE
  A single integer 1-10 representing how well this flow serves its target
  user's goal. This is the ONLY number that appears in the report.

  Calibration:
    9-10  best-in-class — every screen contributes, no friction
    7-8   solid — minor friction, no blockers
    5-6   usable but multiple Mediums or one High, target user will struggle
    3-4   significant problems — most users will fail or abandon
    1-2   broken — Critical structural problems

PART 2 — FLOW NARRATIVE (4-8 sentences of expert prose)
  Describe the user's actual experience walking through this specific flow.
  This is the VOICE of the report — it must read like a senior strategist's
  written observation, not a checklist or a generic UX paragraph.

  Constraints:
    - 4 to 8 sentences (NEVER fewer than 4, never more than 8).
    - Reference the client and target user by name where natural.
    - Reference specific screen numbers when describing key moments.
    - Describe what the user feels, hits, has to think about — not just
      what's on screen.
    - Avoid generic UX jargon. Don't say "the user is presented with"
      twelve times. Write naturally.
    - End with the single most consequential issue or strength.

PART 3 — CURATED FINDINGS
  Deduplicate the two analysts' findings:
    - If two findings describe the same element/issue, MERGE them into one
      under the more specific category and drop the duplicate.
    - Each remaining finding appears EXACTLY ONCE.
    - Keep severity calibration consistent: High = costs conversions or
      blocks task completion; Medium = noticeable friction; Low = polish.
    - Never invent findings. If a category is empty after curation, omit it.

  Output every finding with:
    `category`       — one of: Consistency, Drop-off Risk, Missing States,
                       CTA Clarity, Progress, Navigation, Feedback,
                       Accessibility, Goal Alignment, Mobile Risk,
                       Landing Relevance, Performance, Contextual.
    `severity`       — High | Medium | Low (NEVER Critical at flow level).
    `screen_numbers` — array of 1-indexed screens it concerns.
    `position`       — {{ "screen_number": <int>, "x": <float 0-100>,
                          "y": <float 0-100> }} for the annotation pin.
    `finding`        — one neutral, factual sentence. Specific to this flow.

Return JSON matching this exact schema:
{{
  "journey_score":   <int 1-10>,
  "flow_narrative":  "<4-8 sentences of specific expert prose>",
  "findings": [
    {{
      "category": "...",
      "severity": "High" | "Medium" | "Low",
      "screen_numbers": [<int>, ...],
      "position": {{ "screen_number": <int>, "x": <float>, "y": <float> }},
      "finding": "<one specific sentence>"
    }}
  ]
}}"""


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
    objective_output: Dict,
    contextual_output: Dict,
    flow_metadata: Optional[Dict] = None,
    screens: Optional[List[Dict]] = None,
) -> Dict:
    """Synthesise objective + contextual findings into the final flow report.

    Args:
        objective_output:   {"findings": [...], "journey_metrics": {...}}
        contextual_output:  {"findings": [...]}
        flow_metadata:      flow-level metadata dict
        screens:            ordered list of {"image_path", ...} (optional —
                            included so the model can SEE the journey when
                            writing the narrative).

    Returns:
        {"journey_score": int, "flow_narrative": str, "findings": [...]}
        plus journey_metrics passed through from the objective analyst.
    """
    flow_metadata = flow_metadata or {}
    system_prompt = _build_system_prompt(flow_metadata)

    objective_findings = (objective_output or {}).get("findings") or []
    contextual_findings = (contextual_output or {}).get("findings") or []

    # Load screenshots so the synthesiser can write a narrative that
    # actually reflects the journey.
    images = []
    if screens:
        for s in screens:
            path = (s or {}).get("image_path")
            if path and os.path.exists(path):
                try:
                    images.append(Image.open(path))
                except Exception:
                    continue

    user_blocks = [
        "OBJECTIVE FINDINGS:\n" + json.dumps(objective_findings, indent=2),
        "CONTEXTUAL FINDINGS:\n" + json.dumps(contextual_findings, indent=2),
        f"JOURNEY METRICS: {json.dumps((objective_output or {}).get('journey_metrics', {}))}",
        "Produce the FINAL flow analysis JSON now.",
    ]
    contents = images + user_blocks if images else user_blocks

    try:
        model = genai.GenerativeModel(
            MODEL,
            system_instruction=system_prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        response = model.generate_content(contents)
        out = json.loads(response.text)
    except Exception as e:
        print(f"[flow_synthesiser] error: {e} — falling back to merged findings")
        out = {
            "journey_score": None,
            "flow_narrative": "",
            "findings": objective_findings + contextual_findings,
            "error": str(e),
        }

    # Defensive shape-fixup.
    findings = out.get("findings") or []
    cleaned = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        f.setdefault("category", "Contextual")
        if f["category"] not in CATEGORIES:
            f["category"] = "Contextual"
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
    out.setdefault("journey_score", None)
    out.setdefault("flow_narrative", "")
    out["journey_metrics"] = (objective_output or {}).get("journey_metrics", {})
    return out

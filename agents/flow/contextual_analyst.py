"""
Contextual Flow Analyst
=======================

Companion to the objective analyst. While the objective analyst evaluates
the journey against universal UX heuristics, this one evaluates the same
journey against the BUSINESS / USER context: who's coming to this flow,
where from, on what device, with what mindset, and how well the experience
matches that.

Dimensions evaluated:
  - Traffic source mindset: does the journey assume an awareness level the
    target user actually has?
  - Landing page relevance: does the entry screen meet the user's expectation
    formed by where they came from?
  - Goal alignment: is the screen sequence the shortest plausible path to
    the user_goal?
  - Mobile interruption risk: on phones, are there moments where the user
    losing context (notification, app switch) would force restart?
  - Technical performance signals: any visible jank — long load placeholders,
    layout shifts, broken assets?
  - Contextual factors: anything specific to this client/sector that the
    flow gets right or wrong (regulatory cues, trust signals, jargon level,
    locale norms).

Returns a flat findings list with category labels drawn from CATEGORIES.
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

CATEGORIES = [
    "Goal Alignment",
    "Mobile Risk",
    "Landing Relevance",
    "Contextual",
    "Performance",
]


SYSTEM_PROMPT_TEMPLATE = """You are a senior UX strategist running CONTEXTUAL journey analysis.

You are given:
  - A sequence of screenshots in the order the user encounters them.
  - For each: a state name and a screen number (1-indexed).
  - The flow metadata (flow name, client, platform, target user, user goal).

Flow context:
  - Flow name : {flow_name}
  - Client    : {client}
  - Platform  : {platform}
  - Target    : {target_user}
  - Goal      : {user_goal}

Your job is NOT to evaluate this against universal UX heuristics — that's
a sister agent's job. Your job is to evaluate it against the BUSINESS &
USER context: the target user's mindset, the channel they arrive through,
the device they use, and the goal they came to accomplish.

Evaluate these contextual dimensions. Emit findings only when something
real is at stake — never invent observations to fill a slot.

1. TRAFFIC SOURCE MINDSET — Given the target user description, does the
   journey assume the right level of awareness? (e.g. an "Existing customer,
   mobile-first" probably arrives expecting login-already-known; a "First-
   time visitor" needs more onboarding context.)
   → category: "Contextual"

2. LANDING PAGE RELEVANCE — Does the FIRST screen meet the expectation a
   user who came here to {user_goal} would have? Is the value proposition
   clear before they have to commit?
   → category: "Landing Relevance"

3. GOAL ALIGNMENT — Is this sequence the shortest plausible path to
   {user_goal}? Are there steps that don't move the user toward that
   goal? Misaligned content, premature upsells, off-topic forms.
   → category: "Goal Alignment"

4. MOBILE INTERRUPTION RISK — On a phone, are there moments where losing
   context (notification pulling the app away, screen-lock, OTP arriving in
   another app) would force a restart of the flow? Long forms with no draft,
   timers without pause, content that doesn't survive a backgrounding.
   → category: "Mobile Risk"

5. TECHNICAL PERFORMANCE SIGNALS — Anything visible in the screenshots
   suggesting jank: long-running placeholder UI, low-resolution assets,
   layout shifts, broken icons, blocking error messages.
   → category: "Performance"

6. CONTEXTUAL FACTORS — Sector / client-specific considerations: regulatory
   cues (consent banners, T&C placement) for finance; trust signals for
   commerce; locale conventions; jargon level appropriate to the target
   user; cultural norms.
   → category: "Contextual"

For EVERY finding:
  - `category`       — EXACTLY one of: Goal Alignment, Mobile Risk,
                       Landing Relevance, Contextual, Performance.
  - `severity`       — High | Medium | Low.
  - `screen_numbers` — array of 1-indexed screens it concerns.
  - `position`       — {{ "screen_number": <int>, "x": <0-100>, "y": <0-100> }}.
                       Pick the single most representative screen + element.
  - `finding`        — one neutral, factual, SPECIFIC sentence. Reference
                       the target user / client by name when relevant — this
                       is your edge over a generic UX checklist.

Return JSON matching this exact schema:
{{
  "findings": [
    {{
      "category": "...",
      "severity": "High" | "Medium" | "Low",
      "screen_numbers": [<int>, ...],
      "position": {{ "screen_number": <int>, "x": <float>, "y": <float> }},
      "finding": "<one neutral, contextual sentence>"
    }}
  ]
}}

Quality bar:
  - Reference the user/client/goal by name in your findings when relevant.
  - No findings about generic UX heuristics — those belong to the sister
    agent. Only emit context-dependent observations here.
  - If this flow is well-aligned to its context, emit fewer findings rather
    than padding."""


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
    flow_metadata: Optional[Dict] = None,
) -> Dict:
    """Run the contextual analyst.

    Args:
        screens: ordered list of {"image_path", "state_name", "screen_number"}.
        flow_metadata: flow-level metadata dict.

    Returns:
        {"findings": [...]}
    """
    if not screens:
        return {"findings": []}

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
        return {"findings": []}

    parts = [
        "FLOW JOURNEY (in order):\n" + "\n".join(journey_lines),
        "Produce the CONTEXTUAL flow analysis JSON now.",
    ]
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
        print(f"[contextual_analyst] error: {e}")
        return {"findings": [], "error": str(e)}

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
    return {"findings": cleaned}

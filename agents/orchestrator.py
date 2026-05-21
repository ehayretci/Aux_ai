"""
Orchestrator Agent
==================

Runs BEFORE the 5 specialists. Looks at the screenshot and decides which
specialists should actually evaluate it. The goal is to skip irrelevant
agents on simple screens — saving tokens, reducing duplicate findings,
and producing a final report that's proportional to the screen's actual
complexity.

Decision rules (encoded in the system prompt):
- Usability:               run unless the screen has zero interactive elements
- Visual Design:           always run
- Information Architecture: skip if fewer than 3 distinct content groups /
                            navigation elements
- Onboarding:              skip if the screen is clearly not part of an
                            initial user journey (success/error/settings)
- Accessibility:           skip only if the screen has a single element
                            with no text, colour, or interactive complexity

The orchestrator is conservative: when in doubt, include the agent.

Output schema:
{
  "screen_type":          "single_action" | "form" | "navigation" | ...,
  "complexity":           "low" | "medium" | "high",
  "complexity_reasoning": "<one sentence>",
  "agents_to_run":        ["usability", "visual_design", ...],
  "agents_skipped":       [{"agent": "...", "reason": "..."}, ...]
}
"""

import os
import json
from typing import Dict, List

import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

MODEL = "gemini-2.5-flash"

# All known specialists keyed by the SAME identifier evaluate.py uses.
KNOWN_AGENTS = [
    "usability",
    "information_architecture",
    "visual_design",
    "onboarding",
    "accessibility",
]

SYSTEM_PROMPT = """You are a triage agent in a multi-agent UX evaluation pipeline.

Five specialist agents are available; each evaluates a screen through ONE narrow lens:
- usability             — Functional usability & Nielsen heuristics
- information_architecture — Content groupings, hierarchy, navigation
- visual_design         — Typography, colour, spacing, visual rhythm
- onboarding            — First-time user clarity, time-to-value
- accessibility         — Contrast, touch targets, alt text, etc.

Look at the screenshot and decide which specialists actually have something
useful to say about THIS specific screen. Skipping an agent that has nothing
to evaluate prevents repeated findings and keeps the report focused.

ASSESSMENT (do this internally before deciding):
1. Visual complexity — count distinct UI elements (buttons, fields, cards,
   icons, navigation items, text blocks, etc.).
2. Content density — sparse (1-3 elements) or rich (many elements, forms,
   data, navigation).
3. Screen type — classify as exactly one of:
   splash, single_action, form, navigation, data_display,
   confirmation, error, content, dashboard

DECISION RULES (apply each independently):
- usability:                run UNLESS the screen has zero interactive elements
                            (e.g. a pure splash with no buttons or links)
- visual_design:            ALWAYS run — every screen has visual properties
- information_architecture: skip ONLY IF there are fewer than 3 distinct content
                            groupings or navigation elements
- onboarding:               skip if the screen is clearly NOT part of the initial
                            user journey (confirmation, success, settings, error)
- accessibility:            skip ONLY IF the screen has a single element with no
                            text, colour, or interactive complexity worth checking

Be CONSERVATIVE: when in doubt, include the agent. It is better to run an
unnecessary specialist than to miss a finding. Only skip an agent if you can
state a clear, specific reason.

Return JSON matching this exact schema:
{
  "screen_type": "splash | single_action | form | navigation | data_display | confirmation | error | content | dashboard",
  "complexity": "low | medium | high",
  "complexity_reasoning": "<one sentence>",
  "agents_to_run": ["<agent>", "<agent>", ...],
  "agents_skipped": [
    {"agent": "<agent>", "reason": "<one short sentence>"}
  ]
}

The union of `agents_to_run` and the agents listed in `agents_skipped` MUST
equal exactly: usability, information_architecture, visual_design,
onboarding, accessibility. Each appears exactly once."""


def _validate(plan: Dict) -> Dict:
    """Make sure the plan covers every known agent exactly once.

    The orchestrator is conservative — if anything is missing or malformed,
    we fall back to running ALL specialists rather than dropping any.
    """
    if not isinstance(plan, dict):
        plan = {}

    plan.setdefault("screen_type", "unknown")
    plan.setdefault("complexity", "medium")
    plan.setdefault("complexity_reasoning", "")
    to_run = plan.get("agents_to_run") or []
    skipped = plan.get("agents_skipped") or []

    seen = set()
    for a in to_run:
        if a in KNOWN_AGENTS:
            seen.add(a)
    for s in skipped:
        if isinstance(s, dict) and s.get("agent") in KNOWN_AGENTS:
            seen.add(s["agent"])

    # Visual design must always run — patch if missing.
    if "visual_design" not in to_run:
        to_run = list(to_run) + ["visual_design"]
        skipped = [s for s in skipped if s.get("agent") != "visual_design"]

    # If any agent wasn't mentioned at all, run it (conservative default).
    for a in KNOWN_AGENTS:
        if a not in seen:
            to_run.append(a)

    # Dedupe to_run while preserving order.
    deduped = []
    for a in to_run:
        if a in KNOWN_AGENTS and a not in deduped:
            deduped.append(a)

    plan["agents_to_run"] = deduped
    plan["agents_skipped"] = [
        s for s in skipped
        if isinstance(s, dict) and s.get("agent") in KNOWN_AGENTS
        and s["agent"] not in deduped
    ]
    return plan


def run(image_path: str) -> Dict:
    """Triage a screenshot and decide which specialists to run.

    Returns a validated plan. On any error, falls back to running every
    specialist (the safe default).
    """
    try:
        img = Image.open(image_path)
        model = genai.GenerativeModel(
            MODEL,
            system_instruction=SYSTEM_PROMPT,
            generation_config={"response_mime_type": "application/json"},
        )
        response = model.generate_content(
            [img, "Triage this screen and return the plan."]
        )
        plan = json.loads(response.text)
    except Exception as e:
        print(f"[orchestrator] failed: {e} — running every specialist as fallback")
        plan = {
            "screen_type": "unknown",
            "complexity": "medium",
            "complexity_reasoning": f"orchestrator failed: {e}",
            "agents_to_run": list(KNOWN_AGENTS),
            "agents_skipped": [],
        }
    return _validate(plan)

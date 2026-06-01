"""
Flow Intelligence Agent
=======================

Reviews an entire user-flow as a single experience: every screenshot, the
state and screen number it belongs to, and (optionally) the per-screen
specialist verdicts. Returns a journey-level evaluation covering step count,
consistency, drop-off risk, missing states, redundancy and a short
narrative.

Designed to be runnable WITHOUT prior per-screen evaluations — when none are
supplied the agent leans entirely on the screenshots and the flow metadata.

The model name is a constant at the top of the module (`MODEL`).
"""

import os
import json
from typing import List, Dict, Optional

import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

MODEL = "gemini-2.5-flash"


SYSTEM_PROMPT_TEMPLATE = """You are a senior UX strategist reviewing a complete user flow.

You are given:
- A sequence of screenshots in the order the user encounters them.
- For each screenshot: a state name and a screen number.
- (Optionally) the existing per-screen specialist findings.
- The flow metadata (flow name, sector, platform, target user, user goal).

Flow context:
- Flow name : {flow_name}
- Sector    : {sector}
- Platform  : {platform}
- Target    : {target_user}
- Goal      : {user_goal}
{competitor_block}{notes_block}

Evaluate the COMPLETE journey, not just any single screen:

1. Step count efficiency — How many steps does the flow take?
   What is the typical benchmark for this flow type in this sector
   (e.g. "credit card application in banking" usually takes ~5 steps)?
   Decide whether this flow is `above`, `on-par`, or `below` benchmark.

2. Consistency — Are labels, button styles, terminology and layout patterns
   consistent across screens? List specific inconsistencies found.

3. Drop-off risk — Which screens introduce a notable jump in complexity,
   required input or cognitive load compared to the previous screen?
   Those are drop-off risk points.

4. Missing states — What states appear to be missing from the flow?
   Common omissions: error states, empty states, back navigation,
   confirmation screens, loading states.

5. Redundancy — Is any information requested from the user more than once?
   Are there steps that repeat what was already done?

6. Flow narrative — A 3–4 sentence plain-English description of what
   the user experiences in this flow.

Severity ratings: Critical / High / Medium / Low.

Return JSON matching this exact schema:
{{
  "flow_name": "{flow_name}",
  "sector": "{sector}",
  "total_steps": <int>,
  "benchmark_steps": <int>,
  "benchmark_comparison": "above" | "below" | "on-par",
  "journey_score": <int 1-10>,
  "journey_score_rationale": "<one sentence>",
  "flow_narrative": "<3-4 sentences>",
  "consistency_score": <int 1-10>,
  "consistency_issues": [
    {{"screen_numbers": [<int>, ...], "issue": "<short>", "severity": "Critical|High|Medium|Low"}}
  ],
  "drop_off_risks": [
    {{"screen_number": <int>, "reason": "<short>", "severity": "Critical|High|Medium|Low"}}
  ],
  "missing_states": [
    {{"missing_state": "<short>", "expected_after_screen": <int>, "severity": "Critical|High|Medium|Low"}}
  ],
  "redundancies": [
    {{"screen_numbers": [<int>, ...], "issue": "<short>"}}
  ],
  "positive_patterns": ["<short string>", ...]
}}"""


def _build_system_prompt(flow_metadata: Dict) -> str:
    fm = flow_metadata or {}
    competitor_url = (fm.get("competitor_url") or "").strip()
    notes = (fm.get("notes") or "").strip()
    competitor_block = f"- Competitor reference: {competitor_url}\n" if competitor_url else ""
    notes_block = f"- Notes      : {notes}\n" if notes else ""
    return SYSTEM_PROMPT_TEMPLATE.format(
        flow_name=fm.get("flow_name") or "Untitled Flow",
        sector=fm.get("sector") or "Unspecified",
        platform=fm.get("platform") or "Unspecified",
        target_user=fm.get("target_user") or "Unspecified",
        user_goal=fm.get("user_goal") or "Unspecified",
        competitor_block=competitor_block,
        notes_block=notes_block,
    )


def run(
    screens: List[Dict],
    flow_metadata: Optional[Dict] = None,
    screen_evaluations: Optional[List[Dict]] = None,
) -> Dict:
    """Run the flow analyst.

    Args:
        screens: Ordered list of {"image_path", "state_name", "screen_number"}.
        flow_metadata: {flow_name, sector, platform, target_user, user_goal,
                        competitor_url?, notes?}.
        screen_evaluations: Optional per-screen evaluate() outputs in the same
                            order as `screens`.

    Returns: JSON dict matching the schema documented in the system prompt.
    """
    if not screens:
        raise ValueError("flow_analyst.run requires at least one screen")

    flow_metadata = flow_metadata or {}
    system_prompt = _build_system_prompt(flow_metadata)

    # Build the user-message payload: textual journey description + interleaved images.
    images = []
    journey_lines = []
    for i, s in enumerate(screens, start=1):
        path = s.get("image_path")
        if not path or not os.path.exists(path):
            print(f"[flow_analyst] skipping missing image: {path}")
            continue
        try:
            images.append(Image.open(path))
        except Exception as e:
            print(f"[flow_analyst] could not open {path}: {e}")
            continue
        journey_lines.append(
            f"  {i}. State: {s.get('state_name', '?')} — Screen {s.get('screen_number', i)}"
        )

    if not images:
        raise FileNotFoundError("No screenshots could be opened for the flow")

    parts = [
        "FLOW JOURNEY (in order):\n" + "\n".join(journey_lines),
    ]
    if screen_evaluations:
        # Trim to keep token usage sane — pass top fields only.
        compact = [
            {
                "screen_number": (screens[i] or {}).get("screen_number", i + 1),
                "state_name": (screens[i] or {}).get("state_name"),
                "overall_score": ev.get("overall_score"),
                "score_rationale": ev.get("score_rationale"),
                "critical_issues_count": ev.get("critical_issues_count"),
                # Keep only first 8 findings per screen to stay bounded.
                "findings": (ev.get("findings") or [])[:8],
            }
            for i, ev in enumerate(screen_evaluations)
            if ev
        ]
        parts.append(
            "PER-SCREEN SPECIALIST FINDINGS:\n" + json.dumps(compact, indent=2)
        )
    else:
        parts.append("(No per-screen specialist findings provided — judge from the screens alone.)")

    parts.append("Produce the final flow analysis JSON now.")

    user_blocks = parts[:1]
    # Insert images interleaved between the description and the rest.
    contents = [user_blocks[0]] + images + parts[1:]

    model = genai.GenerativeModel(
        MODEL,
        system_instruction=system_prompt,
        generation_config={"response_mime_type": "application/json"},
    )
    response = model.generate_content(contents)
    return json.loads(response.text)

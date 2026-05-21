"""
Synthesiser Agent
=================

Takes the combined findings from the specialist agents that the orchestrator
selected for this screen, plus the orchestrator's own complexity / screen-type
context, and produces a final verdict: an overall score, a one-line rationale,
the count of Critical issues, and the single highest-priority finding.

It is also responsible for DEDUPLICATING findings so each issue appears
exactly once (under its most relevant category) and for keeping the total
finding count proportional to screen complexity.
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

SYSTEM_PROMPT = """You are a senior UX lead reviewing the combined output of the specialist
agents that the orchestrator selected for this screen. Each specialist
evaluated through ONE narrow lens (Functional Usability, Information
Architecture, Visual Design, Onboarding & Time-to-Value, or Accessibility).

Your job has TWO parts: (a) weigh the combined findings to produce a final
verdict, and (b) curate the finding set itself.

CURATION RULES — apply BEFORE producing the verdict:
1. Each finding must appear EXACTLY ONCE across the entire report, under the
   single most relevant category. If two findings describe the same element
   or the same issue, MERGE them into one finding under the more specific
   category and DROP the duplicate.
2. The total number of findings MUST be proportional to screen complexity:
   - low complexity     → maximum 6 findings (across all categories combined)
   - medium complexity  → maximum 12 findings
   - high complexity    → no hard cap (but stay focused — quality over volume)
3. If a category's specialist did not produce a meaningful finding for this
   screen, that category MUST stay empty. Do NOT generate placeholder or
   forced observations to fill it.
4. Specialists that the orchestrator SKIPPED for this screen produce no
   findings; do not invent any on their behalf.

Scoring guide for overall_score (1–10):
  9–10: best-in-class; minor polish issues only
  7–8:  solid; some friction but no blocking issues
  5–6:  usable but flawed; multiple Medium or one High
  3–4:  significant problems; user will struggle
  1–2:  broken; Critical issues block task completion

Return JSON matching this exact schema:
{
  "curated_findings": [
    {
      "element":  "<short label of the UI element>",
      "position": {"x": <float 0-100>, "y": <float 0-100>},
      "finding":  "<one neutral factual sentence>",
      "severity": "Critical" | "High" | "Medium" | "Low",
      "polarity": "positive" | "negative",
      "category": "<one of the specialist category labels>"
    }
  ],
  "overall_score":         <int 1-10>,
  "score_rationale":       "<one sentence explaining the score>",
  "critical_issues_count": <int>,
  "top_priority_finding":  "<the single most important issue across all categories, in one sentence>"
}

`curated_findings` MUST be the deduplicated, proportional set you produce.
It REPLACES the input list when the report is rendered."""


def run(
    image_path: str,
    all_findings: List[Dict],
    complexity: Optional[str] = None,
    screen_type: Optional[str] = None,
    agents_run: Optional[List[str]] = None,
) -> Dict:
    """Synthesise + curate the combined findings.

    Args:
        image_path:     Path to the screenshot all specialists analysed.
        all_findings:   Flat list of finding dicts produced by the specialists
                        the orchestrator selected.
        complexity:     "low" | "medium" | "high" (from the orchestrator).
        screen_type:    Orchestrator-classified screen type, if known.
        agents_run:     List of specialist keys that actually ran for this
                        screen. Useful for the synthesiser to know which
                        categories are "expected" vs. correctly empty.

    Returns:
        Dict with curated_findings + verdict fields. Callers should use
        `curated_findings` in place of the raw `all_findings`.
    """
    img = Image.open(image_path)
    model = genai.GenerativeModel(
        MODEL,
        system_instruction=SYSTEM_PROMPT,
        generation_config={"response_mime_type": "application/json"},
    )

    user_prompt = (
        "Here is the screen the specialists analysed.\n\n"
        f"COMPLEXITY (from orchestrator): {complexity or 'unknown'}\n"
        f"SCREEN TYPE (from orchestrator): {screen_type or 'unknown'}\n"
        f"SPECIALISTS THAT RAN: {', '.join(agents_run or []) or 'unknown'}\n\n"
        "RAW FINDINGS FROM THOSE SPECIALISTS:\n"
        f"{json.dumps(all_findings, indent=2)}\n\n"
        "Curate (dedupe + cap by complexity) and produce the final synthesis."
    )

    response = model.generate_content([img, user_prompt])
    return json.loads(response.text)

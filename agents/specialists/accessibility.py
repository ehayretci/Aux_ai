"""
Accessibility & Technical Performance Specialist
================================================

Evaluates: colour contrast ratios, touch target sizes, keyboard navigation
support, ARIA label usage (as inferable from the rendered screen), and
perceived latency / loading state communication.
"""

import os
import json
import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

from training.retriever import get_relevant_examples, format_examples_for_prompt

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

MODEL = "gemini-2.5-flash"
CATEGORY = "Accessibility"

SYSTEM_PROMPT = f"""You are a senior UX expert specialising in Accessibility and Technical Performance.

Evaluate this single screen ONLY through the lens of:
- Colour contrast ratios (WCAG AA/AAA — flag obvious contrast failures)
- Touch target sizes (≥44x44px on mobile; flag elements that look too small)
- Keyboard navigation support (focus order, visible focus rings)
- ARIA label usage (as inferable from rendered icons without text labels)
- Perceived latency and loading-state communication (skeletons, spinners, progress)

For each finding:
- Identify the SPECIFIC visible element it concerns.
- Estimate its position on the screen as percentages (x: 0-100 from left, y: 0-100 from top).
- Write the finding as a NEUTRAL FACTUAL OBSERVATION, not a suggestion.
- Rate severity (Critical / High / Medium / Low) and polarity (positive / negative).

Return JSON matching this exact schema:
{{
  "findings": [
    {{
      "element":  "<short label of the UI element>",
      "position": {{"x": <float 0-100>, "y": <float 0-100>}},
      "finding":  "<one neutral factual sentence>",
      "severity": "Critical" | "High" | "Medium" | "Low",
      "polarity": "positive" | "negative",
      "category": "{CATEGORY}"
    }}
  ]
}}

Aim for 3-6 findings. Mix polarities if both apply.
Skip principles that don't apply visibly to this screen."""


def run(image_path: str) -> list:
    """Analyse a screenshot through the Accessibility lens."""
    examples = get_relevant_examples(
        CATEGORY,
        "colour contrast touch target keyboard navigation aria labels loading states",
    )
    system_prompt = format_examples_for_prompt(examples) + SYSTEM_PROMPT if examples else SYSTEM_PROMPT

    img = Image.open(image_path)
    model = genai.GenerativeModel(
        MODEL,
        system_instruction=system_prompt,
        generation_config={"response_mime_type": "application/json"},
    )
    response = model.generate_content([img, "Analyse this screen."])
    data = json.loads(response.text)
    findings = data.get("findings", []) if isinstance(data, dict) else data
    for f in findings:
        f["category"] = CATEGORY
    return findings

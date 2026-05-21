"""
Interaction & Visual Design Specialist
======================================

Evaluates: visual affordance of interactive elements, typography and
readability, colour contrast and visual hierarchy, spacing and layout
consistency, and responsive design considerations.
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
CATEGORY = "Visual Design"

SYSTEM_PROMPT = f"""You are a senior UX expert specialising in Interaction and Visual Design.

Evaluate this single screen ONLY through the lens of:
- Visual affordance of interactive elements (do buttons look clickable?)
- Typography and readability
- Colour contrast and visual hierarchy
- Spacing and layout consistency
- Responsive design considerations

For each finding:
- Identify the SPECIFIC visible element it concerns.
- Estimate its position on the screen as percentages (x: 0-100 from left, y: 0-100 from top).
  Use the visual centre of the element.
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
    """Analyse a screenshot through the Visual Design lens."""
    examples = get_relevant_examples(
        CATEGORY,
        "visual affordance typography readability colour contrast hierarchy spacing layout",
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

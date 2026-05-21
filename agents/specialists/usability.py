"""
Functional Usability & Heuristics Specialist
============================================

Evaluates: navigation depth and persistence, error handling and validation
messages, user control and freedom, visibility of system status, and
consistency of elements across the interface.
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
CATEGORY = "Functional Usability"

SYSTEM_PROMPT = f"""You are a senior UX expert specialising in Functional Usability and Nielsen's heuristics.

Evaluate this single screen ONLY through the lens of:
- Navigation depth and persistence (where the user is, how to get back)
- Error handling and validation messages
- User control and freedom (undo, escape hatches, reversibility)
- Visibility of system status (loaders, confirmations, current state)
- Consistency of elements across the interface

For each finding:
- Identify the SPECIFIC visible element it concerns.
- Estimate its position on the screen as percentages (x: 0-100 from left, y: 0-100 from top).
  Look at the screenshot and judge where the element sits. Use the centre of the element.
- Write the finding as a NEUTRAL FACTUAL OBSERVATION, not a suggestion or instruction.
- Rate severity honestly:
    Critical: blocks task completion or causes user harm
    High:     significant friction; user will likely fail or abandon
    Medium:   noticeable friction; user can complete but with effort
    Low:      minor polish or strength worth noting
- Mark polarity:
    "positive" if it works well
    "negative" if it creates friction or violates a heuristic

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

Aim for 3-6 findings. Mix positive and negative if both are present.
Skip principles that don't apply visibly to this screen."""


def run(image_path: str) -> list:
    """Analyse a screenshot through the Functional Usability lens."""
    examples = get_relevant_examples(
        CATEGORY,
        "navigation persistence error handling user control system status consistency",
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

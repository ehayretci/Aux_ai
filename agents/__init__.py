"""
UX Analysis Agents
==================

Five specialist agents — each evaluates a single screen through ONE narrow
lens — plus a synthesiser that weighs their combined output into a final
verdict.

Specialists (each returns a flat list of findings):
- specialists.usability          — Functional Usability & Heuristics
- specialists.information_arch   — Information Architecture
- specialists.visual_design      — Interaction & Visual Design
- specialists.onboarding         — Onboarding & Time-to-Value
- specialists.accessibility      — Accessibility & Technical Performance

Synthesiser (sees the combined findings + the screen itself):
- synthesiser — overall_score, score_rationale, critical_issues_count

Why one lens per agent?
A model asked to evaluate holistically tends to soften criticism and hedge
praise so the output stays "balanced". Forcing each lens to commit fully
to its perspective produces sharper raw material for the synthesiser to
weigh. See `evaluate.py` for the orchestration.
"""

from .specialists import usability, information_arch, visual_design, onboarding, accessibility
from .specialists import SPECIALISTS
from . import synthesiser
from . import flow_analyst

# Convenience aliases — the `_run` shape mirrors the previous API.
usability_run = usability.run
information_arch_run = information_arch.run
visual_design_run = visual_design.run
onboarding_run = onboarding.run
accessibility_run = accessibility.run
synthesiser_run = synthesiser.run
flow_analyst_run = flow_analyst.run

__all__ = [
    "usability",
    "information_arch",
    "visual_design",
    "onboarding",
    "accessibility",
    "synthesiser",
    "flow_analyst",
    "SPECIALISTS",
    "usability_run",
    "information_arch_run",
    "visual_design_run",
    "onboarding_run",
    "accessibility_run",
    "synthesiser_run",
    "flow_analyst_run",
]

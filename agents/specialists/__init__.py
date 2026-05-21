"""
Specialist Agents
=================

Five single-lens specialists. Each evaluates a screenshot through ONE
narrow expertise and returns a flat list of findings tagged with its
category. The orchestrator (`evaluate.py`) runs them in parallel and the
synthesiser stitches their combined output into a final verdict.
"""

from . import usability
from . import information_arch
from . import visual_design
from . import onboarding
from . import accessibility

SPECIALISTS = [
    usability,
    information_arch,
    visual_design,
    onboarding,
    accessibility,
]

__all__ = [
    "usability",
    "information_arch",
    "visual_design",
    "onboarding",
    "accessibility",
    "SPECIALISTS",
]

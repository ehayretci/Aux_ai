"""
Flow Analysis Pipeline
======================

Three agents working together to produce the flow-level report:

  objective_analyst   — universal UX heuristics across the journey
  contextual_analyst  — business / user / channel context (target user,
                        client, traffic-source mindset, mobile risk, etc.)
  flow_synthesiser    — dedupes both analysts' findings, produces the
                        journey score and the 4-8-sentence narrative.

`run_flow_analysis(...)` is the single entry point. The two analysts run
concurrently via `asyncio.gather`. Step events are emitted in strict
order: preparing_context → objective_analysis → contextual_analysis →
synthesising → generating_report. The server's SSE channel relays them
to the loading UI.
"""

import asyncio
import sys
from typing import Awaitable, Callable, Dict, List, Optional

from . import objective_analyst, contextual_analyst, flow_synthesiser


# Canonical SSE step keys for the flow loader, in strict emit order.
FLOW_STEP_ORDER = [
    "preparing_context",
    "objective_analysis",
    "contextual_analysis",
    "synthesising",
    "generating_report",
]


ProgressCb = Optional[Callable[[str], Awaitable[None]]]


async def _emit(cb: ProgressCb, step: str) -> None:
    if cb is None:
        return
    try:
        result = cb(step)
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[flow] progress callback error on '{step}': {e}", file=sys.stderr)


async def run_flow_analysis(
    screens: List[Dict],
    screen_evaluations: Optional[List[Dict]] = None,
    flow_metadata: Optional[Dict] = None,
    progress_cb: ProgressCb = None,
) -> Dict:
    """Run the 3-agent flow analysis pipeline.

    Args:
        screens:            ordered list of {"image_path", "state_name",
                            "screen_number"}.
        screen_evaluations: optional per-screen evaluation outputs (only the
                            objective analyst uses them).
        flow_metadata:      flow-level metadata dict.
        progress_cb:        async callback invoked with each step key once
                            that step is complete. Always emitted in
                            FLOW_STEP_ORDER even though the two analysts
                            run concurrently.

    Returns:
        Dict shaped:
          {
            "journey_score":   int,
            "flow_narrative":  str,
            "findings":        [...],
            "journey_metrics": {...},
            "objective":       <raw objective output>,
            "contextual":      <raw contextual output>,
            "flow_metadata":   <input metadata>
          }
    """
    flow_metadata = flow_metadata or {}

    # Step 1: preparing context. Trivial work — just acknowledges that the
    # server has resolved screenshots, metadata, and per-screen evaluations
    # and is ready to fan out to the analysts.
    await _emit(progress_cb, "preparing_context")

    # Steps 2 & 3: objective + contextual run in parallel. Their step
    # events emit in strict order (objective then contextual) regardless
    # of which actually finishes first.
    objective_task = asyncio.create_task(
        asyncio.to_thread(
            objective_analyst.run, screens, screen_evaluations, flow_metadata
        )
    )
    contextual_task = asyncio.create_task(
        asyncio.to_thread(
            contextual_analyst.run, screens, flow_metadata
        )
    )

    objective_output = await objective_task
    await _emit(progress_cb, "objective_analysis")

    contextual_output = await contextual_task
    await _emit(progress_cb, "contextual_analysis")

    # Step 4: synthesise.
    final = await asyncio.to_thread(
        flow_synthesiser.run,
        objective_output, contextual_output, flow_metadata, screens,
    )
    await _emit(progress_cb, "synthesising")

    # Step 5: generating report (final marker — caching, return).
    await _emit(progress_cb, "generating_report")

    return {
        "journey_score": final.get("journey_score"),
        "flow_narrative": final.get("flow_narrative", ""),
        "findings": final.get("findings", []),
        "journey_metrics": final.get("journey_metrics", {}),
        "objective": objective_output,
        "contextual": contextual_output,
        "flow_metadata": flow_metadata,
    }


__all__ = [
    "run_flow_analysis",
    "objective_analyst",
    "contextual_analyst",
    "flow_synthesiser",
    "FLOW_STEP_ORDER",
]

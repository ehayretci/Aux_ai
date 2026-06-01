"""
Orchestrator
============

Pipeline (per screen):

  1. Triage (orchestrator)        — sequential, decides which specialists run
  2. Selected specialists         — IO-bound, run in parallel
  3. Synthesiser                  — sequential, dedupes & curates findings

Each phase emits SSE-style events through optional callbacks so the
loading UI can react in real time:

  progress_cb(step: str)              completed-step pings
  event_cb({...})                     arbitrary typed events

Typed events emitted here:
  {"event": "agents_selected", "agents_to_run": [...], "agents_skipped": [...],
   "screen_type": "...", "complexity": "..."}

Steps in order:
  complexity_assessment → (per-agent in agents_to_run) → synthesis → report_generation
"""

import os
import sys
import json
import asyncio
from typing import Awaitable, Callable, Dict, List, Optional

from agents import synthesiser, orchestrator
from agents.specialists import usability, information_arch, visual_design, onboarding, accessibility


# Stable keys used by the SSE progress stream and the orchestrator.
SPECIALIST_PIPELINE = [
    ("usability", usability),
    ("information_architecture", information_arch),
    ("visual_design", visual_design),
    ("onboarding", onboarding),
    ("accessibility", accessibility),
]
SPECIALISTS_BY_KEY = {k: m for k, m in SPECIALIST_PIPELINE}


ProgressCb = Optional[Callable[[str], Awaitable[None]]]
EventCb = Optional[Callable[[dict], Awaitable[None]]]


async def _emit(cb, payload) -> None:
    if cb is None:
        return
    try:
        result = cb(payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        print(f"[evaluate] callback error on '{payload}': {e}", file=sys.stderr)


async def _run_specialist(specialist, image_path: str) -> list:
    try:
        return await asyncio.to_thread(specialist.run, image_path)
    except Exception as e:
        print(f"[{getattr(specialist, 'CATEGORY', '?')}] error: {e}", file=sys.stderr)
        return []


async def evaluate_async(
    image_path: str,
    progress_cb: ProgressCb = None,
    event_cb: EventCb = None,
) -> dict:
    """Triage → selected specialists → synthesise. Returns final dict."""
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Screenshot not found: {image_path}")

    # ---- Phase 1: orchestrator (triage) ------------------------------------
    print(f"[evaluate] running orchestrator on {image_path}…")
    plan: Dict = await asyncio.to_thread(orchestrator.run, image_path)
    agents_to_run: List[str] = plan.get("agents_to_run") or []
    agents_skipped = plan.get("agents_skipped") or []
    complexity = plan.get("complexity")
    screen_type = plan.get("screen_type")

    # Tell the UI exactly which specialists will run before they fire.
    await _emit(event_cb, {
        "event": "agents_selected",
        "agents_to_run": agents_to_run,
        "agents_skipped": agents_skipped,
        "screen_type": screen_type,
        "complexity": complexity,
    })
    await _emit(progress_cb, "complexity_assessment")

    # ---- Phase 2: selected specialists in parallel -------------------------
    print(f"[evaluate] running specialists: {agents_to_run}")

    async def run_and_signal(key: str) -> list:
        mod = SPECIALISTS_BY_KEY.get(key)
        if not mod:
            return []
        findings = await _run_specialist(mod, image_path)
        await _emit(progress_cb, key)
        return findings

    findings_lists = await asyncio.gather(
        *[run_and_signal(k) for k in agents_to_run]
    ) if agents_to_run else []

    # Flatten + sanitise. Drop malformed findings rather than crash the run.
    sanitized: list = []
    for findings in findings_lists:
        for f in findings:
            if not isinstance(f, dict):
                continue
            if "element" not in f or "position" not in f or "finding" not in f:
                continue
            if not isinstance(f.get("position"), dict):
                continue
            if "x" not in f["position"] or "y" not in f["position"]:
                continue
            f.setdefault("severity", "Medium")
            f.setdefault("polarity", "negative")
            f.setdefault("category", "Uncategorised")
            sanitized.append(f)

    # ---- Phase 3: synthesiser (curates + verdict) --------------------------
    print(f"[evaluate] {len(sanitized)} raw findings → synthesiser…")
    verdict = await asyncio.to_thread(
        synthesiser.run,
        image_path,
        sanitized,
        complexity,
        screen_type,
        agents_to_run,
    )
    await _emit(progress_cb, "synthesis")

    # Use the synthesiser's curated, deduped findings. Fall back to the raw
    # set ONLY if the synthesiser failed to return the field at all — an
    # empty list is a legitimate signal from the synthesiser (e.g. a splash
    # screen with truly nothing to report).
    curated = verdict.get("curated_findings")
    if not isinstance(curated, list):
        curated = sanitized
    for f in curated:
        f.setdefault("severity", "Medium")
        f.setdefault("polarity", "negative")
        f.setdefault("category", "Uncategorised")

    await _emit(progress_cb, "report_generation")

    return {
        "image_path": image_path,
        "overall_score": verdict.get("overall_score"),
        "score_rationale": verdict.get("score_rationale", ""),
        "critical_issues_count": verdict.get("critical_issues_count", 0),
        "findings": curated,
        "orchestrator": {
            "screen_type": screen_type,
            "complexity": complexity,
            "complexity_reasoning": plan.get("complexity_reasoning", ""),
            "agents_to_run": agents_to_run,
            "agents_skipped": agents_skipped,
        },
    }


def evaluate(image_path: str) -> dict:
    """Sync wrapper around `evaluate_async`. Avoid calling from inside an event loop."""
    return asyncio.run(evaluate_async(image_path))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python evaluate.py <screenshot_path>")
        sys.exit(1)

    print(json.dumps(evaluate(sys.argv[1]), indent=2))

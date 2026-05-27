"""
UX Analyser FastAPI Server
==========================

Single server bridging the Chrome extension and the Python evaluation engine.

It serves:
  • Existing screenshot capture & session-management endpoints used by the
    Chrome extension and canvas (`/capture`, `/api/sessions`, `/api/session/...`).
  • New analysis endpoints (`/analyse/screen`, `/analyse/flow`).
  • A Server-Sent-Events progress channel
    (`/analyse/flow/progress/{session_id}`) the loading UI subscribes to.
  • The static canvas + flow-archive UIs (templates + /static/...).
  • The standalone per-screen HTML report (`/api/report?screenshot_path=...`).

Run with `./start_server.sh` (uvicorn on port 8000).
"""

import os
import json
import base64
import shutil
import asyncio
import uuid
from pathlib import Path
from typing import Optional, List, Dict, AsyncGenerator

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    JSONResponse,
    HTMLResponse,
    Response,
    FileResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from evaluate import evaluate_async
from agents import flow_analyst                # legacy single-pass agent (kept for back-compat)
from agents.flow import run_flow_analysis      # new 3-agent flow pipeline
from report import build_report


# ------------------------------------------------------------------ paths --
SCREENSHOTS_DIR = "screenshots"
TEMPLATES = Jinja2Templates(directory="templates")


def session_dir(session_id: str) -> str:
    return os.path.join(SCREENSHOTS_DIR, session_id)


def metadata_path(session_id: str) -> str:
    return os.path.join(session_dir(session_id), "metadata.json")


def analyses_path(session_id: str) -> str:
    return os.path.join(session_dir(session_id), "analyses.json")


def flow_metadata_path(session_id: str) -> str:
    return os.path.join(session_dir(session_id), "flow_metadata.json")


def flow_analysis_path(session_id: str) -> str:
    return os.path.join(session_dir(session_id), "flow_analysis.json")


# ------------------------------------------------------------------ JSON I/O
def load_metadata(session_id: str) -> Dict:
    path = metadata_path(session_id)
    if not os.path.exists(path):
        return {"sessionId": session_id, "states": {}, "isRecording": False}
    with open(path, "r") as f:
        meta = json.load(f)
    meta.setdefault("isRecording", False)
    return meta


def save_metadata(session_id: str, meta: Dict) -> None:
    os.makedirs(session_dir(session_id), exist_ok=True)
    with open(metadata_path(session_id), "w") as f:
        json.dump(meta, f, indent=2)


def load_analyses(session_id: str) -> Dict:
    path = analyses_path(session_id)
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def save_analyses(session_id: str, analyses: Dict) -> None:
    os.makedirs(session_dir(session_id), exist_ok=True)
    with open(analyses_path(session_id), "w") as f:
        json.dump(analyses, f, indent=2)


def load_flow_metadata(session_id: str) -> Dict:
    path = flow_metadata_path(session_id)
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)


def save_flow_metadata(session_id: str, fm: Dict) -> None:
    os.makedirs(session_dir(session_id), exist_ok=True)
    with open(flow_metadata_path(session_id), "w") as f:
        json.dump(fm, f, indent=2)


# ----------------------------------------------------------- SSE registry --
# Each session_id has a list of subscribed queues. Multiple SSE clients may
# subscribe to the same session at once (e.g. canvas + extension popup).
_progress_subscribers: Dict[str, List[asyncio.Queue]] = {}
_subscriber_lock = asyncio.Lock()


async def _broadcast_progress(session_id: str, event: Dict) -> None:
    """Push a progress event to every subscriber of `session_id`."""
    queues = _progress_subscribers.get(session_id, [])
    for q in list(queues):
        try:
            q.put_nowait(event)
        except Exception:
            pass


def _make_progress_callback(session_id: str, completed: List[str]):
    """Return an async callback that emits an SSE event each time `step` completes.

    `completed` is the running list of steps already finished — used to emit
    `current=true` for the next step in the pipeline.
    """
    async def cb(step: str) -> None:
        completed.append(step)
        await _broadcast_progress(session_id, {
            "step": step,
            "completed": True,
            "current": False,
        })
    return cb


def _make_event_callback(session_id: str):
    """Return an async callback that broadcasts arbitrary typed events.

    Used for `{"event": "agents_selected", ...}` payloads and any future
    typed events emitted by `evaluate_async`.
    """
    async def cb(event: dict) -> None:
        await _broadcast_progress(session_id, event)
    return cb


# --------------------------------------------------------------- FastAPI ---
app = FastAPI(title="UX Analyser", version="3.0")

# CORS: open for the Chrome extension and any local dev tool.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static + screenshots are served under fixed prefixes.
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")

# Brand assets (logos) live in Contents/ alongside the project.
_CONTENTS_DIR = Path(__file__).resolve().parent / "Contents"
if _CONTENTS_DIR.is_dir():
    app.mount("/contents", StaticFiles(directory=str(_CONTENTS_DIR)), name="contents")

# Theme is shared across pages + the Chrome extension popup. Persist to disk
# so it survives server restarts.
_THEME_FILE = Path(__file__).resolve().parent / ".aux_theme"

def _read_theme() -> str:
    try:
        v = _THEME_FILE.read_text().strip().lower()
        return "light" if v == "light" else "dark"
    except Exception:
        return "dark"

@app.get("/api/theme")
def get_theme():
    return {"theme": _read_theme()}

@app.post("/api/theme")
async def set_theme(req: Request):
    body = await req.json()
    theme = "light" if str(body.get("theme", "")).lower() == "light" else "dark"
    try:
        _THEME_FILE.write_text(theme)
    except Exception:
        pass
    return {"theme": theme}


# --------------------------------------------------------------- pydantic --
class CaptureBody(BaseModel):
    sessionId: str
    image: str
    url: Optional[str] = "unknown"
    trigger: Optional[str] = "unknown"
    state: Optional[int] = 1
    screenshotIndex: Optional[int] = 1
    click: Optional[Dict] = None


class RecordingBody(BaseModel):
    isRecording: bool = False


class FlowMetadata(BaseModel):
    flow_name: Optional[str] = None
    client: Optional[str] = None      # new — replaces "sector" in the UI
    sector: Optional[str] = None      # kept for backward compatibility
    platform: Optional[str] = None
    target_user: Optional[str] = None
    user_goal: Optional[str] = None
    competitor_url: Optional[str] = None
    notes: Optional[str] = None


class AnalyseScreenBody(BaseModel):
    image_base64: Optional[str] = None
    screenshot_path: Optional[str] = None
    screen_id: Optional[str] = None
    state_name: Optional[str] = None
    screen_number: Optional[int] = None
    flow_metadata: Optional[FlowMetadata] = None
    session_id: Optional[str] = None  # used as SSE channel key


class AnalyseFlowScreen(BaseModel):
    image_base64: Optional[str] = None
    screenshot_path: Optional[str] = None
    screen_id: Optional[str] = None
    state_name: Optional[str] = None
    screen_number: Optional[int] = None


class AnalyseFlowBody(BaseModel):
    session_id: Optional[str] = None
    screens: List[AnalyseFlowScreen] = Field(default_factory=list)
    flow_metadata: Optional[FlowMetadata] = None


# ---------------------------------------------------------------- routes ---
@app.get("/health")
async def health():
    return {"status": "ok"}


# ----- existing capture / session management (renamed but compatible) -----
@app.post("/capture")
async def capture(body: CaptureBody):
    image_data_url = body.image
    try:
        _, encoded = image_data_url.split(",", 1)
        image_bytes = base64.b64decode(encoded)
    except Exception as e:
        raise HTTPException(400, f"Invalid image format: {e}")

    state_dir = os.path.join(session_dir(body.sessionId), f"state_{body.state}")
    os.makedirs(state_dir, exist_ok=True)

    filename = f"{str(body.screenshotIndex).zfill(2)}_{body.trigger}.png"
    filepath = os.path.join(state_dir, filename)
    with open(filepath, "wb") as f:
        f.write(image_bytes)

    relpath = os.path.relpath(filepath, SCREENSHOTS_DIR).replace(os.sep, "/")

    meta = load_metadata(body.sessionId)
    state_key = f"state_{body.state}"
    state_entry = meta["states"].setdefault(
        state_key,
        {"state": body.state, "url": body.url, "screenshots": []},
    )
    if body.trigger == "intro":
        state_entry["url"] = body.url
    state_entry.setdefault("name", f"State {body.state}")

    state_entry["screenshots"].append({
        "index": body.screenshotIndex,
        "trigger": body.trigger,
        "url": body.url,
        "filename": filename,
        "path": relpath,
        "click": body.click,
    })
    state_entry["screenshots"].sort(key=lambda s: s["index"])
    save_metadata(body.sessionId, meta)

    print(f"[{body.sessionId}] state_{body.state}/{filename}  (url: {body.url})"
          + (f"  click=({body.click.get('clientX')}, {body.click.get('clientY')})" if body.click else ""))
    return {"status": "success", "filepath": filepath}


@app.post("/analyze")
async def legacy_analyze(request: Request):
    """Legacy 'flow finished' marker — extension hits this on Stop."""
    data = await request.json()
    session_id = data.get("sessionId")
    if not session_id:
        raise HTTPException(400, "Missing sessionId")
    sdir = session_dir(session_id)
    if not os.path.exists(sdir):
        raise HTTPException(404, "Session directory not found")

    states = sorted([d for d in os.listdir(sdir) if os.path.isdir(os.path.join(sdir, d))])
    total = sum(len(os.listdir(os.path.join(sdir, s))) for s in states)
    print(f"\n{'=' * 50}\nFlow stopped: {session_id}\n{'=' * 50}")
    print(f"Total: {len(states)} states, {total} screenshots")

    meta = load_metadata(session_id)
    meta["isRecording"] = False
    save_metadata(session_id, meta)
    return {"status": "success", "session": session_id,
            "message": f"Session has {len(states)} states with {total} total screenshots"}


@app.get("/api/sessions")
async def list_sessions():
    if not os.path.exists(SCREENSHOTS_DIR):
        return {"sessions": []}

    sessions = []
    for d in sorted(os.listdir(SCREENSHOTS_DIR)):
        sdir = os.path.join(SCREENSHOTS_DIR, d)
        if not os.path.isdir(sdir):
            continue

        # Collect screen count + latest score for the archive card.
        meta = load_metadata(d)
        analyses = load_analyses(d)
        flow_meta = load_flow_metadata(d)
        flow_analysis_file = flow_analysis_path(d)
        flow_analysis = None
        if os.path.exists(flow_analysis_file):
            try:
                with open(flow_analysis_file) as f:
                    flow_analysis = json.load(f)
            except Exception:
                flow_analysis = None

        screen_count = sum(
            len((s or {}).get("screenshots", []))
            for s in (meta.get("states") or {}).values()
        )

        # Pick the most recent analysis score, if any.
        last_score = None
        if analyses:
            scores = [v.get("overall_score") for v in analyses.values() if isinstance(v, dict)]
            scores = [s for s in scores if isinstance(s, (int, float))]
            if scores:
                last_score = round(sum(scores) / len(scores))

        sessions.append({
            "id": d,
            "name": flow_meta.get("flow_name") or d,
            "client": flow_meta.get("client") or flow_meta.get("sector"),
            "sector": flow_meta.get("sector"),
            "platform": flow_meta.get("platform"),
            "target_user": flow_meta.get("target_user"),
            "user_goal": flow_meta.get("user_goal"),
            "screen_count": screen_count,
            "is_recording": meta.get("isRecording", False),
            "last_screen_score": last_score,
            "journey_score": (flow_analysis or {}).get("journey_score"),
            "analysed": bool(analyses) or bool(flow_analysis),
            "captured_at": _session_mtime(sdir),
        })

    return {"sessions": sessions}


def _session_mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except Exception:
        return None


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    sdir = session_dir(session_id)
    if not os.path.exists(sdir):
        raise HTTPException(404, "Session not found")
    meta = load_metadata(session_id)
    analyses = load_analyses(session_id)
    meta["analyzedPaths"] = list(analyses.keys())
    meta["flowMetadata"] = load_flow_metadata(session_id)
    flow_analysis_file = flow_analysis_path(session_id)
    if os.path.exists(flow_analysis_file):
        try:
            with open(flow_analysis_file) as f:
                meta["flowAnalysis"] = json.load(f)
        except Exception:
            meta["flowAnalysis"] = None
    return meta


@app.post("/api/session/{session_id}/recording")
async def set_recording(session_id: str, body: RecordingBody):
    meta = load_metadata(session_id)
    meta["isRecording"] = body.isRecording
    save_metadata(session_id, meta)
    print(f"[{session_id}] recording = {body.isRecording}")
    return {"status": "ok", "isRecording": body.isRecording}


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    if "/" in session_id or ".." in session_id:
        raise HTTPException(400, "Invalid session id")
    sdir = session_dir(session_id)
    if not os.path.exists(sdir):
        raise HTTPException(404, "Session not found")
    shutil.rmtree(sdir)
    print(f"Deleted session: {session_id}")
    return {"status": "deleted", "session": session_id}


@app.post("/api/session/{session_id}/flow_metadata")
async def upsert_flow_metadata(session_id: str, body: FlowMetadata):
    save_flow_metadata(session_id, body.model_dump(exclude_none=True))
    return {"status": "ok"}


@app.post("/api/session/{session_id}/state/{state_key}/rename")
async def rename_state(session_id: str, state_key: str, request: Request):
    data = await request.json()
    new_name = (data.get("name") or "").strip()
    if not new_name:
        raise HTTPException(400, "Empty name")
    meta = load_metadata(session_id)
    states = meta.get("states", {})
    if state_key not in states:
        raise HTTPException(404, "State not found")
    states[state_key]["name"] = new_name
    save_metadata(session_id, meta)
    return {"status": "ok"}


# ------------------------------------------------------ analysis endpoints
async def _resolve_image_path(b64: Optional[str], rel_path: Optional[str], session_id: Optional[str]) -> str:
    """Persist `b64` (if given) into the session's manual folder and return path.
    Otherwise return the absolute path for `rel_path` under screenshots/."""
    if rel_path:
        full_path = os.path.join(SCREENSHOTS_DIR, rel_path)
        if not os.path.exists(full_path):
            raise HTTPException(404, f"Screenshot not found: {rel_path}")
        return full_path

    if b64:
        if not session_id:
            raise HTTPException(400, "session_id required when uploading base64 image")
        try:
            _, encoded = b64.split(",", 1) if "," in b64 else (None, b64)
            data = base64.b64decode(encoded)
        except Exception as e:
            raise HTTPException(400, f"Invalid base64: {e}")
        out_dir = os.path.join(session_dir(session_id), "manual")
        os.makedirs(out_dir, exist_ok=True)
        name = f"{uuid.uuid4().hex[:8]}.png"
        out_path = os.path.join(out_dir, name)
        with open(out_path, "wb") as f:
            f.write(data)
        return out_path

    raise HTTPException(400, "Either screenshot_path or image_base64 is required")


async def _persist_analysis(session_id: str, screenshot_path: str, result: Dict) -> None:
    """Cache the result against the relative screenshot_path."""
    if not session_id or not screenshot_path:
        return
    analyses = load_analyses(session_id)
    analyses[screenshot_path] = result
    save_analyses(session_id, analyses)


def _is_new_shape(entry: Dict) -> bool:
    return isinstance(entry, dict) and "findings" in entry and "overall_score" in entry


@app.post("/analyse/screen")
async def analyse_screen(body: AnalyseScreenBody):
    """Run the full per-screen pipeline (5 specialists + synthesiser).

    Streams progress events to the SSE channel keyed by `session_id` while it
    runs, returns the final evaluation in the HTTP response.
    """
    session_id = body.session_id or _session_from_path(body.screenshot_path)
    if body.flow_metadata and session_id:
        save_flow_metadata(session_id, body.flow_metadata.model_dump(exclude_none=True))

    image_path = await _resolve_image_path(body.image_base64, body.screenshot_path, session_id)
    rel_path = body.screenshot_path or os.path.relpath(image_path, SCREENSHOTS_DIR).replace(os.sep, "/")

    # Cache hit fast-path. Replay the SSE timeline from the cached
    # orchestrator plan so the loader fills correctly.
    if session_id and rel_path:
        cached = load_analyses(session_id).get(rel_path)
        if _is_new_shape(cached):
            print(f"[/analyse/screen] cache hit: {rel_path}")
            orch = cached.get("orchestrator") or {}
            agents_to_run = orch.get("agents_to_run") or [
                "usability", "information_architecture", "visual_design", "onboarding", "accessibility"
            ]
            await _broadcast_progress(session_id, {
                "event": "agents_selected",
                "agents_to_run": agents_to_run,
                "agents_skipped": orch.get("agents_skipped", []),
                "screen_type": orch.get("screen_type"),
                "complexity": orch.get("complexity"),
            })
            for k in ["complexity_assessment", *agents_to_run, "synthesis", "report_generation"]:
                await _broadcast_progress(session_id, {"step": k, "completed": True, "current": False})
            return cached

    completed: List[str] = []
    cb = _make_progress_callback(session_id or "no-session", completed) if session_id else None
    event_cb = _make_event_callback(session_id) if session_id else None

    print(f"[/analyse/screen] running pipeline on: {image_path}")
    result = await evaluate_async(image_path, progress_cb=cb, event_cb=event_cb)
    result["screenshot_path"] = rel_path
    result["screen_id"] = body.screen_id
    result["state_name"] = body.state_name
    result["screen_number"] = body.screen_number

    if session_id and rel_path:
        await _persist_analysis(session_id, rel_path, result)
    return result


@app.post("/analyse/flow")
async def analyse_flow(body: AnalyseFlowBody):
    """Run the 3-agent flow analysis pipeline.

    Per-screen evaluations are reused if cached on disk; if not, they are
    skipped here (the user can analyse individual screens separately).
    SSE events emit in strict order regardless of which analyst finishes
    first:
        preparing_context → objective_analysis → contextual_analysis →
        synthesising → generating_report
    """
    session_id = body.session_id
    if body.flow_metadata and session_id:
        save_flow_metadata(session_id, body.flow_metadata.model_dump(exclude_none=True))

    if not body.screens:
        raise HTTPException(400, "screens[] required")

    flow_meta = load_flow_metadata(session_id) if session_id else (
        body.flow_metadata.model_dump(exclude_none=True) if body.flow_metadata else {}
    )

    # Resolve image paths and pull any cached per-screen evaluations. We
    # don't re-run per-screen analysis here — the user analyses screens
    # individually via /analyse/screen.
    screen_evals: List[Dict] = []
    image_paths: List[str] = []
    for s in body.screens:
        try:
            img_path = await _resolve_image_path(s.image_base64, s.screenshot_path, session_id)
        except HTTPException:
            continue
        rel_path = s.screenshot_path or os.path.relpath(img_path, SCREENSHOTS_DIR).replace(os.sep, "/")
        cached = load_analyses(session_id).get(rel_path) if session_id else None
        screen_evals.append(cached if _is_new_shape(cached) else None)
        image_paths.append(img_path)

    # Build the screens list in the shape the flow agents expect.
    flow_screens = [
        {
            "image_path": p,
            "state_name": s.state_name or "",
            "screen_number": s.screen_number or (i + 1),
        }
        for i, (p, s) in enumerate(zip(image_paths, body.screens))
    ]

    # Drive the 5 SSE step events through the flow pipeline.
    progress_cb = _make_progress_callback(session_id or "no-session", []) if session_id else None
    try:
        flow_result = await run_flow_analysis(
            flow_screens, screen_evals, flow_meta, progress_cb=progress_cb
        )
    except Exception as e:
        print(f"[/analyse/flow] pipeline error: {e}")
        flow_result = {"error": str(e), "journey_score": None, "flow_narrative": "", "findings": []}

    # Persist for the canvas's "post-analysis" button state.
    if session_id:
        try:
            with open(flow_analysis_path(session_id), "w") as f:
                json.dump(flow_result, f, indent=2)
        except Exception as e:
            print(f"[/analyse/flow] persist failed: {e}")

    return {
        "session_id": session_id,
        "flow_metadata": flow_meta,
        "screen_evaluations": [e for e in screen_evals if e],
        "flow_analysis": flow_result,
    }


@app.get("/analyse/flow/progress/{session_id}")
async def analyse_flow_progress(session_id: str):
    """SSE stream of progress events for a given session_id.

    Each event is a JSON line of `{step, completed, current}`. The connection
    stays open; the client closes it once it receives the terminal event.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async with _subscriber_lock:
        _progress_subscribers.setdefault(session_id, []).append(queue)

    async def event_gen() -> AsyncGenerator[str, None]:
        try:
            yield ": connected\n\n"  # comment line keeps the stream alive
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            async with _subscriber_lock:
                subs = _progress_subscribers.get(session_id, [])
                if queue in subs:
                    subs.remove(queue)
                if not subs:
                    _progress_subscribers.pop(session_id, None)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _session_from_path(rel_path: Optional[str]) -> Optional[str]:
    if not rel_path:
        return None
    return rel_path.split("/", 1)[0] if "/" in rel_path else rel_path


# ------------------------------------------ report HTML for one screenshot
def _ordered_screen_paths(session_id: str) -> List[Dict]:
    """Return all screens in flow order, each as {path, state_key, state_name, screen_number}."""
    meta = load_metadata(session_id)
    states = meta.get("states", {})
    state_keys = sorted(states.keys(), key=lambda k: int("".join(c for c in k if c.isdigit()) or 0))
    out: List[Dict] = []
    for sk in state_keys:
        s = states[sk]
        state_name = s.get("name") or f"State {s.get('state', '?')}"
        screens = s.get("screenshots", []) or []
        screens_sorted = sorted(screens, key=lambda x: x.get("index", 0))
        for i, sc in enumerate(screens_sorted, start=1):
            out.append({
                "path": sc.get("path"),
                "state_key": sk,
                "state_name": state_name,
                "screen_number": i,
            })
    return out


@app.get("/api/report")
async def report_for_screenshot(screenshot_path: str):
    full_path = os.path.join(SCREENSHOTS_DIR, screenshot_path)
    if not os.path.exists(full_path):
        raise HTTPException(404, "Screenshot not found")
    session_id = _session_from_path(screenshot_path)

    cached = load_analyses(session_id).get(screenshot_path) if session_id else None
    if _is_new_shape(cached):
        result = cached
        result["image_path"] = full_path
    else:
        result = await evaluate_async(full_path)
        result["screenshot_path"] = screenshot_path
        if session_id:
            await _persist_analysis(session_id, screenshot_path, result)

    # Compute the "State X — Screen Y" label and prev/next nav URLs.
    state_label = ""
    flow_position = ""
    prev_url = ""
    next_url = ""
    if session_id:
        ordered = _ordered_screen_paths(session_id)
        idx = next((i for i, s in enumerate(ordered) if s["path"] == screenshot_path), -1)
        if idx >= 0:
            here = ordered[idx]
            state_label = f'{here["state_name"]} — Screen {here["screen_number"]}'
            flow_position = f"Screen {idx + 1} of {len(ordered)}"
            if idx > 0:
                prev_url = f"/api/report?screenshot_path={ordered[idx - 1]['path']}"
            if idx < len(ordered) - 1:
                next_url = f"/api/report?screenshot_path={ordered[idx + 1]['path']}"

    flat = screenshot_path.replace("/", "__").rsplit(".", 1)[0] + ".report.html"
    out_path = os.path.join(session_dir(session_id), flat) if session_id else os.path.join("/tmp", flat)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    build_report(
        result,
        out_path,
        state_label=state_label,
        flow_position=flow_position,
        prev_url=prev_url,
        next_url=next_url,
    )
    with open(out_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ------------------------------------------------------ legacy /api/analyze
@app.post("/api/analyze")
async def legacy_analyze_endpoint(request: Request):
    """Compatibility shim — older canvas.js calls /api/analyze with screenshot_path."""
    data = await request.json()
    body = AnalyseScreenBody(
        screenshot_path=data.get("screenshot_path"),
        screen_id=data.get("screen_id"),
        state_name=data.get("state_name"),
        screen_number=data.get("screen_number"),
        flow_metadata=FlowMetadata(**(data.get("flow_metadata") or {})) if data.get("flow_metadata") else None,
        session_id=data.get("session_id"),
    )
    return await analyse_screen(body)


# ------------------------------------------------------ HTML pages (canvas)
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return TEMPLATES.TemplateResponse(request, "index.html")


@app.get("/canvas", response_class=HTMLResponse)
@app.get("/canvas/{session_id}", response_class=HTMLResponse)
async def canvas(request: Request, session_id: str = ""):
    return TEMPLATES.TemplateResponse(
        request, "canvas.html", {"session_id": session_id or ""}
    )


# ----------------------------------------------------------- startup log --
@app.on_event("startup")
async def _startup_banner():
    print("=" * 60)
    print("  UX Analyser — FastAPI server")
    print("  Local URL :  http://127.0.0.1:8000")
    print("  Health    :  http://127.0.0.1:8000/health")
    print("  Canvas    :  http://127.0.0.1:8000/canvas/<session-name>")
    print("=" * 60)

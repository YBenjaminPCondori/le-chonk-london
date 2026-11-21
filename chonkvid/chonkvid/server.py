"""FastAPI server: live epidemic over WebSocket, replay of recorded runs, results."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, fields
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import forecast
from . import prompt_test
from . import scenario
from .config import ROOT, settings
from .detect import Detector
from .experiment import RESULTS, RUNS, Recorder
from .llm import make_llm
from .persistence import LocalStore, PersistenceError, get_store
from .sim import EpidemicConfig, run_epidemic
from .who import make_who
from .world import build_world

WEB = ROOT / "web"
app = FastAPI(title="ChonkVid")
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/world")
def world_view():
    """Plague-Inc-style world map front-end over the same /ws/run feed."""
    return FileResponse(WEB / "world" / "index.html")


@app.get("/api/status")
def status():
    s = settings()
    return {"mode": s.mode, "key": bool(s.api_key), "threshold": s.threshold,
            "models": {"agent": s.agent_model, "embed": s.embed_model, "who": s.who_model},
            "who_mode": s.who_mode, "who_context": s.who_context, "who_vision": s.who_vision,
            "database": _persistence_status()["remote"], "payload": scenario.PAYLOAD, "hardened": scenario.HARDENED_PROMPT,
            "sample_system": scenario.SAMPLE_SYSTEM,
            "forecast": {"available": forecast.available(), "model": forecast.model_label()}}


@app.get("/api/world")
def world(n: int = 40, seed: int = 0):
    return build_world(max(8, min(n, 120)), seed).to_json()


@app.get("/api/risk")
def risk_forecast(n: int = 40, seed: int = 0):
    """Per attack type: past-run history and each agent's forecast infection risk."""
    from . import risk
    w = build_world(max(8, min(n, 120)), seed).to_json()
    return risk.forecast(w, risk.load_runs())


async def dashboard_report(filename, kind, authorization):
    if authorization:
        project = os.environ.get("SUPABASE_PROJECT_ID")
        if not project:
            raise HTTPException(503, "Configure the dashboard project to read shared reports")
        return await shared_report(UUID(project), kind, authorization)
    path = RESULTS / filename
    if not path.is_file():
        return {"available": False}
    return dict(json.loads(path.read_text()), available=True)


@app.get("/api/results")
async def results(authorization: str | None = Header(None)):
    return await dashboard_report("latest.json", "experiment", authorization)


@app.get("/api/prompt-test")
async def prompt_test_latest(authorization: str | None = Header(None)):
    return await dashboard_report("prompt-test-latest.json", "prompt-test", authorization)


@app.get("/api/runs")
def runs():
    if not RUNS.exists():
        return []
    files = sorted(RUNS.glob("*.jsonl"), reverse=True)[:60]
    return [{"id": f.name, "bytes": f.stat().st_size} for f in files]


@app.get("/api/runs/{run_id}")
async def run(run_id: str, authorization: str | None = Header(None)):
    if authorization:
        store, projects = await _remote_scope(authorization)
        project = os.environ.get("SUPABASE_PROJECT_ID")
        if project not in projects:
            raise HTTPException(404)
        rid = await store.legacy_run_id(project, run_id)
        if rid is None:
            raise HTTPException(404)
        return await store.get_run_events(rid)
    p = (RUNS / run_id).resolve()
    if p.parent != RUNS.resolve() or not p.is_file() or p.suffix != ".jsonl":
        raise HTTPException(404)
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Optional Supabase persistence (additive; local mode is the default and the
# endpoints above keep working with no Supabase configured).
# ---------------------------------------------------------------------------

def _persistence_status() -> dict:
    backend = os.environ.get("PERSISTENCE_BACKEND", "local")
    try:
        store = get_store()
        return {"backend": backend, "remote": store.remote, "configured": True}
    except PersistenceError:
        return {"backend": backend, "remote": False, "configured": False}


@app.get("/api/persistence")
def persistence():
    return _persistence_status()


async def _remote_identity(store, authorization: str | None):
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Supabase access token required",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        user = await store.authenticate(token.strip())
    except PersistenceError as exc:
        raise HTTPException(503, "Supabase authentication unavailable") from exc
    if user is None:
        raise HTTPException(401, "Invalid Supabase access token",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


async def _remote_scope(authorization: str | None):
    try:
        store = get_store()
    except PersistenceError as exc:
        raise HTTPException(503, "Remote persistence unavailable") from exc
    if not store.remote:
        raise HTTPException(503, "PERSISTENCE_BACKEND is local; no remote runs")
    user = await _remote_identity(store, authorization)
    try:
        return store, await store.member_projects(user.id)
    except PersistenceError as exc:
        raise HTTPException(503, "Project membership unavailable") from exc


@app.get("/api/persistence/runs")
async def persistence_runs(limit: int = 60, authorization: str | None = Header(None)):
    store, projects = await _remote_scope(authorization)
    try:
        rows = await store.list_runs(limit=max(1, min(limit, 100)), project_ids=projects)
    except PersistenceError as exc:
        raise HTTPException(503, "Remote runs unavailable") from exc
    return {"runs": [r.to_dict() for r in rows], **_persistence_status()}


@app.get("/api/persistence/runs/{run_id}")
async def persistence_run(run_id: UUID, authorization: str | None = Header(None)):
    store, projects = await _remote_scope(authorization)
    try:
        rec = await store.get_run(str(run_id), project_ids=projects)
        if rec is None:
            raise HTTPException(404, "Run not found")
        events = await store.get_run_events(str(run_id))
    except PersistenceError as exc:
        raise HTTPException(503, "Remote run unavailable") from exc
    return {"run": rec.to_dict(), "events": events, **_persistence_status()}


@app.get("/api/persistence/projects/{project_id}/reports/{kind}")
async def shared_report(project_id: UUID, kind: str, authorization: str | None = Header(None)):
    store, projects = await _remote_scope(authorization)
    if str(project_id) not in projects or kind not in ("experiment", "prompt-test"):
        raise HTTPException(404)
    row = await store.latest_report(str(project_id), kind)
    return dict(row, available=True) if row else {"available": False}


@app.post("/api/test-prompt")
async def test_prompt_endpoint(payload: dict, authorization: str | None = Header(None)):
    store = None
    if authorization:
        store, projects = await _remote_scope(authorization)
        project = os.environ.get("SUPABASE_PROJECT_ID")
        if project not in projects:
            raise HTTPException(403, "Project membership required")
    system = str(payload.get("system") or "").strip()
    if not 1 <= len(system) <= 4000:
        raise HTTPException(422, "system prompt must be 1 to 4000 characters")
    n = max(5, min(int(payload.get("n") or 20), 50))
    s = settings()
    llm = make_llm(s)
    try:
        out = await prompt_test.test_prompt(llm, s, system, n=n,
                                            with_who=bool(payload.get("with_who", True)))
    finally:
        await llm.aclose()
    if store:
        await store.save_report(project, "prompt-test", out)
    else:
        prompt_test.save(out, remote=False)
    return out


def _cfg(data: dict) -> EpidemicConfig:
    allowed = {f.name for f in fields(EpidemicConfig)}
    cfg = EpidemicConfig(**{k: v for k, v in data.items() if k in allowed and v is not None})
    cfg.n = max(8, min(cfg.n, 120))
    cfg.ticks = max(1, min(cfg.ticks, 40))
    # The world view sends a contact pattern per injection and a clicked patient zero:
    # bound them so a stray value cannot multiply model calls or crash the run.
    cfg.contacts_per_tick = max(1, min(int(cfg.contacts_per_tick), 6))
    cfg.infectious_ticks = max(1, min(int(cfg.infectious_ticks), 10))
    cfg.venue_every = max(0, min(int(cfg.venue_every), 10))
    if cfg.patient_zero is not None and not 0 <= int(cfg.patient_zero) < cfg.n:
        cfg.patient_zero = None
    cfg.agent_system = (cfg.agent_system or "")[:4000]
    if cfg.policy not in ("none", "random", "targeted", "risk"):
        cfg.policy = "none"
    if cfg.risk_order is not None:
        cfg.risk_order = [int(a) for a in list(cfg.risk_order)[:200]]
    if data.get("use_who_vaccine"):
        p = RESULTS / "latest.json"
        if p.exists():
            v = json.loads(p.read_text()).get("who_vaccine")
            if v:
                cfg.vaccine, cfg.vaccine_label = v, "who"
    return cfg


@app.websocket("/ws/run")
async def ws_run(ws: WebSocket):
    await ws.accept()
    s = settings()
    try:
        data = await ws.receive_json()
        cfg = _cfg(data)
        pace = float(data.get("pace", 0.5 if s.mode == "mock" else 0.0))
        llm = make_llm(s, seed=cfg.seed)
        det = Detector(llm, s.threshold)
        persist_error = None
        try:
            store = get_store()
        except PersistenceError:
            store = LocalStore()
            persist_error = "persistence backend unavailable"

        mirror = False
        authorization = ws.headers.get("authorization")
        if not authorization and isinstance(data.get("access_token"), str):
            authorization = "Bearer " + data["access_token"]
        if store.remote and authorization:
            try:
                user = await _remote_identity(store, authorization)
                mirror = await store.can_mirror(user)
                if not mirror:
                    persist_error = "remote persistence requires membership in the configured project"
            except HTTPException as exc:
                persist_error = exc.detail
            except PersistenceError:
                persist_error = "remote project membership unavailable"

        # Keep authorized recordings out of the anonymous demo replay directory.
        label = f"{llm.source}-dash-s{cfg.seed}"
        rec = Recorder(f"{label}-{uuid4().hex}",
                       directory=RUNS / "private" if authorization and (
                           store.remote or os.environ.get("PERSISTENCE_BACKEND", "").strip().lower()
                           == "supabase") else RUNS)
        remote_id = None
        pending: list[tuple[int, dict]] = []
        seq = 0
        end_ev = None
        remote_complete = False

        async def fail_remote(message: str):
            nonlocal persist_error
            persist_error = message
            if remote_id:
                try:
                    await store.fail_run(remote_id, message)
                except Exception:
                    persist_error += "; failure status unavailable"

        async def flush_pending():
            if not pending:
                return
            try:
                await store.append_events(remote_id, pending)
            except PersistenceError:
                await store.append_events(remote_id, pending)
            pending.clear()

        try:
            if mirror:
                try:
                    remote_id = await store.create_run(
                        mode=s.mode,
                        simulation_type=os.environ.get("SIMULATION_TYPE", "prompt_injection"),
                        label=label, config=asdict(cfg),
                        model_metadata={"agent": s.agent_model, "embed": s.embed_model,
                                        "who": s.who_model, "threshold": s.threshold,
                                        "source": llm.source})
                    await store.mark_run_running(remote_id)
                except Exception:
                    await fail_remote("remote create_run or start failed")

            async def emit(ev: dict):
                nonlocal seq, end_ev
                rec.write(ev)
                if remote_id and persist_error is None:
                    pending.append((seq, ev))
                    seq += 1
                    if len(pending) >= 100:
                        try:
                            await flush_pending()
                        except Exception:
                            await fail_remote("remote append_events failed; full recording retained locally")
                if ev["type"] == "end":
                    end_ev = ev
                await ws.send_json(ev)

            # Timestral outbreak forecast (optional; never allowed to break a run)
            use_fc = forecast.available()
            fc_hist: list[dict] = []
            fcs: list[dict] = []
            if use_fc:
                try:
                    await asyncio.to_thread(forecast.load)  # warm up before tick 1
                except Exception as e:
                    use_fc = False
                    await emit({"type": "forecast_error", "message": str(e)[:300]})

            async for ev in run_epidemic(llm, det, cfg, who_model=s.who_model,
                                         agent_model=s.agent_model, who=make_who(s, llm)):
                if ev["type"] == "end":
                    # Score before "end" so "end" stays the recording's last event
                    # (replay and risk.load_runs rely on that).
                    for check in forecast.score_all(fcs, fc_hist, cfg.n):
                        await emit(check)
                await emit(ev)
                if (use_fc and ev["type"] == "tick" and ev.get("phase") != "exposed"
                        and isinstance(ev.get("counts"), dict)):
                    try:
                        fc_hist.append({"tick": ev["tick"], "counts": ev["counts"]})
                        fc = await asyncio.to_thread(forecast.predict, list(fc_hist), cfg.n,
                                                     cfg.ticks - ev["tick"])
                    except Exception as e:
                        use_fc = False
                        await emit({"type": "forecast_error", "message": str(e)[:300]})
                    else:
                        if fc:
                            fcs.append(fc)
                            await emit(fc)
                if ev["type"] == "tick" and ev.get("phase") == "resolved" and pace:
                    await asyncio.sleep(pace)

            if remote_id and persist_error is None:
                operation = "append_events"
                try:
                    await flush_pending()
                    operation = "save_result"
                    if end_ev is None or end_ev.get("summary") is None:
                        raise PersistenceError("missing final metrics")
                    await store.save_result(remote_id, metrics={
                        "summary": end_ev["summary"], "usage": end_ev.get("usage"),
                        "source": end_ev.get("source"),
                    })
                    operation = "complete_run"
                    await store.complete_run(remote_id)
                    remote_complete = True
                except Exception:
                    await fail_remote(f"remote {operation} failed; full recording retained locally")

            msg = {"type": "recorded", "id": rec.path.name}
            if remote_id and persist_error is None:
                msg["remote_run_id"] = remote_id
            if persist_error:
                msg["persistence_error"] = persist_error
            await ws.send_json(msg)
        except (Exception, asyncio.CancelledError):
            if remote_id and persist_error is None and not remote_complete:
                await fail_remote("simulation ended before remote completion")
            raise
        finally:
            rec.close()
            await llm.aclose()
    except WebSocketDisconnect:
        return
    except Exception as e:  # surface errors in the UI instead of a silent socket close
        try:
            await ws.send_json({"type": "error", "message": str(e)[:500]})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


@app.post("/api/speak")
async def speak_endpoint(payload: dict):
    """WHO situation report read aloud by Voxtral TTS (voxtral-mini-tts-3-0)."""
    from .voice import speak

    text = str(payload.get("text", "")).strip()[:1500]
    if not text:
        raise HTTPException(400, "text required")
    s = settings()
    if s.mode != "live":
        raise HTTPException(400, "Voxtral needs MISTRAL_API_KEY (mock mode has no audio)")
    llm = make_llm(s)
    try:
        audio = await speak(llm, text)
    finally:
        await llm.aclose()
    return Response(content=audio, media_type="audio/mpeg")

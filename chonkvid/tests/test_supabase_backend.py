"""Tests for the optional Supabase backend (Phases 1-6).

Covers: migration validity (parsed with the real PostgreSQL grammar via pglast),
the persistence store protocol (local + supabase-with-fake-client), run status
transitions, ordered event insertion/replay, duplicate handling, result
persistence, backend failure handling in the WebSocket path, and local-mode
backward compatibility. Live RLS tests against a real Supabase project live in
test_rls_live.py and are skipped unless SUPABASE_TEST_* env vars are set.
"""

import asyncio
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("CHONKVID_MODE", "mock")
_TMP = tempfile.mkdtemp(prefix="chonkvid-supabase-test-")
os.environ.setdefault("CHONKVID_RESULTS_DIR", os.path.join(_TMP, "results"))
os.environ.setdefault("CHONKVID_RUNS_DIR", os.path.join(_TMP, "runs"))

from chonkvid import persistence as P  # noqa: E402
from chonkvid.persistence import (  # noqa: E402
    LocalStore, PersistenceError, PersistenceStore, RunRecord, get_store, reset_store,
    validate_events)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

APP_TABLES = ["profiles", "projects", "project_members", "scenarios",
              "simulation_runs", "simulation_events", "simulation_results",
              "report_submissions"]


def run(c):
    return asyncio.run(c)


# ---------------------------------------------------------------------------
# 1. Migration validity
# ---------------------------------------------------------------------------

def test_migrations_exist_and_parse_with_postgres_grammar():
    pglast = pytest.importorskip("pglast")
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert (MIGRATIONS / "20261121000004_remediation.sql").is_file()
    for f in files:
        stmts = pglast.parse_sql(f.read_text())  # raises on invalid SQL
        assert len(stmts) > 0
        if f.name.endswith("_remediation.sql"):
            pglast.parse_plpgsql(f.read_text())


def test_migration_schema_content():
    schema = (MIGRATIONS / "20261121000001_schema.sql").read_text()
    for t in APP_TABLES:
        assert re.search(rf"create table if not exists public\.{t}\b", schema), t
        assert f"uuid primary key" in schema
    # deterministic replay constraint
    assert "unique (run_id, sequence_number)" in schema
    # status / type checks
    assert "check (simulation_type in ('prompt_injection', 'disease_outbreak'))" in schema
    assert "check (status in ('pending', 'running', 'completed', 'failed'))" in schema
    assert "check (mode in ('mock', 'live'))" in schema
    assert "check (input_type in ('text', 'voice'))" in schema
    # foreign keys to auth users / projects / runs
    assert "references auth.users (id)" in schema
    assert "references public.projects (id) on delete cascade" in schema
    assert "references public.simulation_runs (id) on delete cascade" in schema
    # simulation_results is unique per run via its primary key
    assert re.search(r"create table if not exists public\.simulation_results \(\s*"
                     r"run_id uuid primary key", schema)


def test_migration_rls_content():
    rls = (MIGRATIONS / "20261121000002_rls.sql").read_text()
    for t in APP_TABLES:
        assert f"alter table public.{t} enable row level security;" in rls, t
    # non-recursive membership helpers
    assert "create or replace function public.is_project_member" in rls
    assert "create or replace function public.is_project_admin" in rls
    assert rls.count("security definer") >= 3
    # privilege escalation guards
    assert "role in ('admin', 'member')" in rls          # API can never write 'owner'
    assert "role <> 'owner'" in rls                       # owner row immutable via API
    # events are append-only: an insert policy but no update/delete policy
    assert "for insert with check (public.can_access_run(run_id))" in rls
    assert not re.search(r"on public\.simulation_events\s*\n\s*for (update|delete)", rls)


def test_migration_storage_content():
    storage = (MIGRATIONS / "20261121000003_storage.sql").read_text()
    assert "'simulation-reports', 'simulation-reports', false" in storage
    assert "'voice-recordings', 'voice-recordings', false" in storage
    assert storage.count("on storage.objects") >= 6  # read/upload/delete per bucket
    assert "object_project_id" in storage


# ---------------------------------------------------------------------------
# 5-8. Store protocol: LocalStore and SupabaseStore (with an in-memory client)
# ---------------------------------------------------------------------------

@pytest.fixture
def local_store(tmp_path):
    return LocalStore(runs_dir=tmp_path / "runs", results_dir=tmp_path / "results")


def _events(n, start=0):
    return [(i, {"type": "tick", "tick": i, "counts": {"S": 1, "E": 0, "I": 0, "R": 0}})
            for i in range(start, start + n)]


def test_local_store_run_lifecycle_and_status_transitions(local_store):
    rid = run(local_store.create_run(mode="mock", simulation_type="prompt_injection",
                                    label="t", config={"n": 8},
                                    model_metadata={"agent": "x"}))
    assert rid.endswith(".jsonl")
    assert run(local_store.get_run(rid)).status == "pending"
    run(local_store.mark_run_running(rid))
    assert run(local_store.get_run(rid)).status == "running"
    run(local_store.append_events(rid, _events(3)))
    run(local_store.complete_run(rid))
    rec = run(local_store.get_run(rid))
    assert rec.status == "completed" and rec.mode == "mock"
    assert rec.config == {"n": 8} and rec.model_metadata == {"agent": "x"}
    # replay contract: file exists under runs/ and parses line by line, in order
    events = run(local_store.get_run_events(rid))
    assert [e["sequence_number"] for e in events] == [0, 1, 2]
    assert all(e["type"] == "tick" for e in events)
    assert (local_store.runs_dir / rid).exists()
    run(local_store.save_result(rid, metrics={"attack_rate": 0.5}, report_path=None))
    saved = json.loads((local_store.results_dir / "runs" / f"{rid}.json").read_text())
    assert saved["metrics"]["attack_rate"] == 0.5
    listed = run(local_store.list_runs())
    assert [r.run_id for r in listed] == [rid]


def test_local_store_fail_run_records_error(local_store):
    rid = run(local_store.create_run(mode="live", simulation_type="disease_outbreak"))
    run(local_store.mark_run_running(rid))
    run(local_store.fail_run(rid, "boom"))
    rec = run(local_store.get_run(rid))
    assert rec.status == "failed" and rec.error_message == "boom"
    assert rec.simulation_type == "disease_outbreak"


def test_local_store_rejects_duplicate_and_out_of_order_events(local_store):
    rid = run(local_store.create_run(mode="mock"))
    run(local_store.mark_run_running(rid))
    run(local_store.append_events(rid, _events(2)))          # seq 0, 1
    with pytest.raises(PersistenceError, match="duplicate or out-of-order"):
        run(local_store.append_events(rid, _events(1, start=1)))  # seq 1 again
    with pytest.raises(PersistenceError, match="ascending"):
        run(local_store.append_events(rid, [(3, {"type": "tick"}), (2, {"type": "tick"})]))
    with pytest.raises(PersistenceError, match="duplicate sequence_number in batch"):
        run(local_store.append_events(rid, [(2, {"type": "tick"}), (2, {"type": "tick"})]))


def test_store_input_validation(local_store):
    with pytest.raises(PersistenceError, match="mode"):
        run(local_store.create_run(mode="banana"))
    with pytest.raises(PersistenceError, match="simulation_type"):
        run(local_store.create_run(mode="mock", simulation_type="zombie"))
    with pytest.raises(PersistenceError, match="UUID"):
        P.validate_uuid("nope", "run_id")
    with pytest.raises(PersistenceError):
        validate_events([("x", {"type": "tick"})])
    with pytest.raises(PersistenceError, match="'type'"):
        validate_events([(0, {"nope": 1})])
    with pytest.raises(PersistenceError, match="unknown run"):
        run(local_store.get_run("20260101-000000-missing.jsonl"))


# --- SupabaseStore against a fake in-memory client (no network) ------------

class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self._op = self._rows = self._fields = None
        self._filters, self._order, self._limit, self._on_conflict = [], None, None, None
        self._membership = []
        self._range = slice(None)
        self._columns = "*"
        self._ignore_duplicates = False

    def insert(self, rows):
        self._op, self._rows = "insert", ([rows] if isinstance(rows, dict) else rows)
        return self

    def update(self, fields):
        self._op, self._fields = "update", fields
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self._op, self._rows = "upsert", ([rows] if isinstance(rows, dict) else rows)
        self._on_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates
        return self

    def select(self, cols="*"):
        self._op = "select"
        self._columns = cols
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def in_(self, col, vals):
        self._membership.append((col, vals))
        return self

    def range(self, start, end):
        self._range = slice(start, end + 1)
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        table = self.db.setdefault(self.name, [])
        if self._op == "insert":
            out = []
            for r in self._rows:
                r = dict(r)
                r.setdefault("id", str(uuid.uuid4()))
                r.setdefault("created_at", f"2026-11-21T10:{len(table):02d}:00Z")
                if self.name == "simulation_events":
                    for e in table:
                        if (e["run_id"] == r["run_id"]
                                and e["sequence_number"] == r["sequence_number"]):
                            raise Exception(
                                'duplicate key value violates unique constraint '
                                '"simulation_events_run_id_sequence_number_key" (SQLSTATE 23505)')
                table.append(r)
                out.append(r)
            return FakeResult(out)
        if self._op == "update":
            matched = [r for r in table if all(r.get(c) == v for c, v in self._filters)]
            for r in matched:
                r.update(self._fields)
            return FakeResult(matched)
        if self._op == "upsert":
            out = []
            for r in self._rows:
                keys = (self._on_conflict or "id").split(",")
                existing = next((x for x in table if all(x.get(k) == r.get(k) for k in keys)), None)
                if existing:
                    if not self._ignore_duplicates:
                        existing.update(r)
                    out.append(existing)
                else:
                    r = dict(r)
                    r.setdefault("id", str(uuid.uuid4()))
                    table.append(r)
                    out.append(r)
            return FakeResult(out)
        if self._op == "select":
            rows = [r for r in table if all(r.get(c) == v for c, v in self._filters)]
            if self._order:
                col, desc = self._order
                rows = sorted(rows, key=lambda r: r.get(col), reverse=desc)
            rows = [r for r in rows if all(r.get(c) in vals for c, vals in self._membership)]
            if self._limit:
                rows = rows[: self._limit]
            rows = rows[self._range]
            if self._columns != "*" and not self._columns.startswith("*,"):
                rows = [{key: r.get(key) for key in self._columns.split(",")} for r in rows]
            return FakeResult(rows)
        raise AssertionError("no op set")


class FakeClient:
    def __init__(self):
        self.db = {}

    def table(self, name):
        return FakeTable(self.db, name)


@pytest.fixture
def supa_store():
    ss = pytest.importorskip("chonkvid.supabase_store")
    store = ss.SupabaseStore(
        url="https://fake.supabase.co", service_role_key="test-service-key",
        project_id=str(uuid.uuid4()), scenario_id=str(uuid.uuid4()),
        simulation_type="prompt_injection")
    store._client = FakeClient()  # replace the real SDK client; no network in tests
    return store


def test_supabase_store_requires_valid_config():
    ss = pytest.importorskip("chonkvid.supabase_store")
    with pytest.raises(PersistenceError, match="https"):
        ss.SupabaseStore(url="http://insecure", service_role_key="k")
    with pytest.raises(PersistenceError, match="service_role_key"):
        ss.SupabaseStore(url="https://x.supabase.co", service_role_key="")
    with pytest.raises(PersistenceError, match="simulation_type"):
        ss.SupabaseStore(url="https://x.supabase.co", service_role_key="k",
                         simulation_type="zombie")


def test_supabase_store_requires_env(monkeypatch):
    ss = pytest.importorskip("chonkvid.supabase_store")
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(PersistenceError, match="SUPABASE_URL"):
        ss.supabase_config_from_env()


def test_supabase_store_run_lifecycle_ordered_replay_and_result(supa_store):
    rid = run(supa_store.create_run(mode="mock", simulation_type="prompt_injection",
                                    config={"n": 8}, model_metadata={"agent": "a"}))
    assert re.match(r"^[0-9a-f-]{36}$", rid)
    run(supa_store.mark_run_running(rid))
    assert run(supa_store.get_run(rid)).status == "running"
    events = _events(5)
    run(supa_store.append_events(rid, events[:3]))
    run(supa_store.append_events(rid, events[3:]))   # batched append, still ordered
    replayed = run(supa_store.get_run_events(rid))
    assert [e["sequence_number"] for e in replayed] == [0, 1, 2, 3, 4]
    assert [e["tick"] for e in replayed] == [0, 1, 2, 3, 4]
    run(supa_store.complete_run(rid))
    assert run(supa_store.get_run(rid)).status == "completed"
    run(supa_store.save_result(rid, metrics={"summary": {"attack_rate": 0.25}},
                              report_path="simulation-reports/p/run/report.md"))
    assert supa_store._client.db["simulation_results"][0]["run_id"] == rid
    assert supa_store._client.db["simulation_results"][0]["report_path"].endswith("report.md")
    listed = run(supa_store.list_runs(limit=10))
    assert [r.run_id for r in listed] == [rid]


def test_supabase_store_event_retries_are_idempotent(supa_store):
    rid = run(supa_store.create_run(mode="mock"))
    run(supa_store.mark_run_running(rid))
    run(supa_store.append_events(rid, _events(2)))
    run(supa_store.append_events(rid, _events(1, start=1)))
    assert len(run(supa_store.get_run_events(rid))) == 2
    with pytest.raises(PersistenceError, match="different content"):
        run(supa_store.append_events(rid, [(1, {"type": "end"})]))


def test_supabase_store_fail_run_and_validation(supa_store):
    rid = run(supa_store.create_run(mode="live", simulation_type="disease_outbreak"))
    run(supa_store.fail_run(rid, "engine exploded"))
    rec = run(supa_store.get_run(rid))
    assert rec.status == "failed" and rec.error_message == "engine exploded"
    with pytest.raises(PersistenceError, match="mode"):
        run(supa_store.create_run(mode="banana"))
    with pytest.raises(PersistenceError, match="UUID"):
        run(supa_store.get_run("nope"))
    with pytest.raises(PersistenceError, match="unknown run"):
        run(supa_store.get_run(str(uuid.uuid4())))


def test_supabase_store_batching(supa_store):
    supa_store.batch_size = 2
    rid = run(supa_store.create_run(mode="mock"))
    run(supa_store.append_events(rid, _events(5)))  # must be split into chunks of 2
    assert len(supa_store._client.db["simulation_events"]) == 5


# ---------------------------------------------------------------------------
# Store factory
# ---------------------------------------------------------------------------

def test_get_store_defaults_to_local(monkeypatch):
    monkeypatch.delenv("PERSISTENCE_BACKEND", raising=False)
    reset_store()
    store = get_store()
    assert isinstance(store, LocalStore) and store.remote is False
    reset_store()


def test_get_store_unknown_backend_and_missing_supabase_env(monkeypatch):
    monkeypatch.setenv("PERSISTENCE_BACKEND", "carrier-pigeon")
    reset_store()
    with pytest.raises(PersistenceError, match="PERSISTENCE_BACKEND"):
        get_store()
    reset_store()
    monkeypatch.setenv("PERSISTENCE_BACKEND", "supabase")
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(PersistenceError, match="SUPABASE_URL"):
        get_store()
    reset_store()


# ---------------------------------------------------------------------------
# 9-10. Server integration: WS contract preserved, failures explicit
# ---------------------------------------------------------------------------

class FakeRemoteStore(PersistenceStore):
    """In-memory remote store used to exercise the server mirror path."""

    remote = True

    def __init__(self, fail_on_create=False, fail_on_append=False):
        self.fail_on_create = fail_on_create
        self.fail_on_append = fail_on_append
        self.runs = {}
        self.events = {}
        self.results = {}
        self._seq = 0

    async def authenticate(self, token):
        return SimpleNamespace(id=token) if token in {"member", "outsider"} else None

    async def member_projects(self, user_id):
        return ["project-a"] if user_id == "member" else []

    async def can_mirror(self, user):
        return "project-a" in await self.member_projects(user.id)

    async def create_run(self, *, mode, simulation_type="prompt_injection", label="",
                         config=None, model_metadata=None, scenario_id=None):
        if self.fail_on_create:
            raise PersistenceError("supabase unreachable")
        rid = str(uuid.uuid4())
        self.runs[rid] = RunRecord(run_id=rid, status="pending", mode=mode,
                                   simulation_type=simulation_type, label=label, project_id="project-a",
                                   config=config or {}, model_metadata=model_metadata or {})
        return rid

    async def mark_run_running(self, run_id):
        self.runs[run_id].status = "running"

    async def append_events(self, run_id, events):
        if self.fail_on_append:
            raise PersistenceError("append failed")
        batch = validate_events(events)
        self.events.setdefault(run_id, []).extend(batch)

    async def complete_run(self, run_id):
        self.runs[run_id].status = "completed"

    async def fail_run(self, run_id, error_message):
        self.runs[run_id].status = "failed"
        self.runs[run_id].error_message = error_message

    async def get_run(self, run_id, *, project_ids):
        rec = self.runs.get(run_id)
        return rec if rec and rec.project_id in project_ids else None

    async def list_runs(self, limit=60, *, project_ids):
        return [r for r in self.runs.values() if r.project_id in project_ids][:limit]

    async def get_run_events(self, run_id):
        return [dict(ev, sequence_number=seq) for seq, ev in self.events.get(run_id, [])]

    async def save_result(self, run_id, metrics, report_path=None):
        self.results[run_id] = {"metrics": metrics, "report_path": report_path}


def _ws_run_once(client, payload=None, token=None):
    payload = dict(payload or {"policy": "targeted", "k": 4, "n": 16, "ticks": 3, "pace": 0})
    if token:
        payload["access_token"] = token
    with client.websocket_connect("/ws/run") as ws:
        ws.send_json(payload)
        msgs = []
        while True:
            ev = ws.receive_json()
            msgs.append(ev)
            if ev["type"] in ("recorded", "error"):
                return msgs


def test_websocket_local_mode_contract_unchanged(monkeypatch):
    from fastapi.testclient import TestClient
    from chonkvid import server
    monkeypatch.setattr(server, "get_store", lambda: LocalStore())
    monkeypatch.setattr("chonkvid.server.get_store", lambda: LocalStore())
    c = TestClient(server.app)
    assert c.get("/api/persistence").json()["backend"] == "local"
    assert c.get("/api/persistence").json()["remote"] is False
    msgs = _ws_run_once(c)
    assert msgs[0]["type"] == "start" and msgs[-1]["type"] == "recorded"
    rec = msgs[-1]
    assert rec["id"].endswith(".jsonl")
    assert "remote_run_id" not in rec and "persistence_error" not in rec
    # replay API still serves the recording
    events = c.get(f"/api/runs/{rec['id']}").json()
    assert events[0]["type"] == "start" and any(e["type"] == "end" for e in events)
    assert c.get("/api/persistence/runs").status_code == 503


def test_websocket_mirrors_to_remote_store(monkeypatch):
    from fastapi.testclient import TestClient
    from chonkvid import server
    fake = FakeRemoteStore()
    monkeypatch.setattr(server, "get_store", lambda: fake)
    c = TestClient(server.app)
    msgs = _ws_run_once(c, token="member")
    rec = msgs[-1]
    assert rec["type"] == "recorded" and "persistence_error" not in rec
    rid = rec["remote_run_id"]
    run_rec = fake.runs[rid]
    assert run_rec.status == "completed" and run_rec.mode == "mock"
    seqs = [s for s, _ in fake.events[rid]]
    assert seqs == list(range(len(seqs)))               # ordered, gapless
    ws_event_types = [m["type"] for m in msgs if m["type"] != "recorded"]
    assert [ev["type"] for _, ev in fake.events[rid]] == ws_event_types
    assert rid in fake.results and fake.results[rid]["metrics"]["summary"]
    # local recording preserved alongside the remote mirror
    assert (server.RUNS / "private" / rec["id"]).is_file()
    assert c.get(f"/api/runs/{rec['id']}").status_code == 404
    # additive read endpoints work in remote mode
    assert c.get("/api/persistence").json()["remote"] is True
    remote = c.get(f"/api/persistence/runs/{rid}",
                   headers={"Authorization": "Bearer member"}).json()
    assert remote["run"]["run_id"] == rid
    assert [e["sequence_number"] for e in remote["events"]] == seqs


def test_websocket_reports_persistence_failure_without_failing_run(monkeypatch):
    from fastapi.testclient import TestClient
    from chonkvid import server
    fake = FakeRemoteStore(fail_on_create=True)
    monkeypatch.setattr(server, "get_store", lambda: fake)
    c = TestClient(server.app)
    msgs = _ws_run_once(c, token="member")
    rec = msgs[-1]
    assert rec["type"] == "recorded"                     # run still completes locally
    assert "remote_run_id" not in rec
    assert "persistence_error" in rec and "create_run" in rec["persistence_error"]
    assert (server.RUNS / "private" / rec["id"]).is_file()
    assert c.get(f"/api/runs/{rec['id']}").status_code == 404


def test_websocket_marks_run_failed_when_simulation_aborts(monkeypatch):
    from fastapi.testclient import TestClient
    from chonkvid import server

    async def boom(*a, **k):
        yield {"type": "start", "config": {}, "world": {"nodes": [], "edges": []},
               "vaccinated": [], "patient_zero": 0, "source": "mock"}
        raise RuntimeError("engine exploded")

    fake = FakeRemoteStore()
    monkeypatch.setattr(server, "get_store", lambda: fake)
    monkeypatch.setattr(server, "run_epidemic", boom)
    c = TestClient(server.app)
    msgs = _ws_run_once(c, token="member")
    assert msgs[-1]["type"] == "error" and "engine exploded" in msgs[-1]["message"]
    rid = next(iter(fake.runs))
    assert fake.runs[rid].status == "failed"
    assert fake.runs[rid].error_message


def test_existing_endpoints_smoke(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from chonkvid import server
    monkeypatch.setattr(server, "get_store", lambda: LocalStore())
    # Other tests in the same session may run an experiment that writes results;
    # this smoke test checks the empty case, so give it its own results folder.
    monkeypatch.setattr(server, "RESULTS", tmp_path)
    c = TestClient(server.app)
    assert c.get("/api/status").json()["mode"] == "mock"
    assert len(c.get("/api/world?n=12&seed=0").json()["nodes"]) == 12
    assert isinstance(c.get("/api/runs").json(), list)
    assert c.get("/api/results").json()["available"] is False


def test_remote_access_requires_valid_member(monkeypatch):
    from fastapi.testclient import TestClient
    from chonkvid import server

    store = FakeRemoteStore()
    monkeypatch.setattr(server, "get_store", lambda: store)
    client = TestClient(server.app)
    member_run = _ws_run_once(client, token="member")[-1]["remote_run_id"]
    other_run = str(uuid.uuid4())
    store.runs[other_run] = RunRecord(
        run_id=other_run, status="completed", mode="mock", project_id="project-b")

    for token in (None, "invalid", "outsider"):
        message = _ws_run_once(client, token=token)[-1]
        assert "remote_run_id" not in message
    assert len(store.runs) == 2

    list_path = "/api/persistence/runs"
    for path in (list_path, f"{list_path}/{member_run}"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401

    member = {"Authorization": "Bearer member"}
    outsider = {"Authorization": "Bearer outsider"}
    assert [row["run_id"] for row in client.get(list_path, headers=member).json()["runs"]] == [member_run]
    assert client.get(list_path, headers=outsider).json()["runs"] == []
    assert client.get(f"{list_path}/{member_run}", headers=member).status_code == 200
    assert client.get(f"{list_path}/{member_run}", headers=outsider).status_code == 404
    assert client.get(f"{list_path}/{other_run}?project_id=project-b", headers=member).status_code == 404


@pytest.mark.parametrize("operation,failed_sequence", [
    ("append_events", 0), ("append_events", 100), ("append_events", 200),
    ("save_result", None), ("complete_run", None),
])
def test_incomplete_mirror_is_never_reported_completed(monkeypatch, operation, failed_sequence):
    from fastapi.testclient import TestClient
    from chonkvid import server

    async def events(*args, **kwargs):
        for i in range(205):
            yield {"type": "tick", "tick": i}
        yield {"type": "end", "summary": {"attack_rate": 0.1}}

    store = FakeRemoteStore()
    append = store.append_events

    async def reject(*args, **kwargs):
        raise PersistenceError("injected failure")

    async def append_or_reject(run_id, batch):
        if batch[0][0] == failed_sequence:
            await reject()
        await append(run_id, batch)

    monkeypatch.setattr(store, operation, append_or_reject if operation == "append_events" else reject)
    monkeypatch.setattr(server, "get_store", lambda: store)
    monkeypatch.setattr(server, "run_epidemic", events)
    message = _ws_run_once(TestClient(server.app), token="member")[-1]

    assert message["type"] == "recorded"
    assert operation in message["persistence_error"]
    assert "remote_run_id" not in message
    record, = store.runs.values()
    assert record.status == "failed"
    assert operation in record.error_message
    recording = server.RUNS / "private" / message["id"]
    assert len(recording.read_text().splitlines()) == 206


def test_bootstrap_reuses_sdk_user_and_context_after_restart():
    from chonkvid.supabase_store import SupabaseStore

    class FakeAdmin:
        def __init__(self):
            self.users = []

        def create_user(self, attributes):
            if self.users:
                raise RuntimeError("user already exists")
            user = SimpleNamespace(id=str(uuid.uuid4()), email=attributes["email"])
            self.users.append(user)
            return SimpleNamespace(user=user)

        def list_users(self, page, per_page):
            return self.users if page == 1 else []

    client = FakeClient()
    admin = FakeAdmin()
    client.auth = SimpleNamespace(admin=admin)

    def new_store():
        store = SupabaseStore(url="https://fake.supabase.co", service_role_key="test-service-key")
        store._client = client
        return store

    first, second = new_store(), new_store()
    run(first.create_run(mode="mock"))
    run(second.create_run(mode="mock"))
    assert len(admin.users) == 1
    assert len(client.db["projects"]) == len(client.db["scenarios"]) == 1
    assert first._project_id == second._project_id
    assert first._scenario_id == second._scenario_id


def test_supabase_auth_verifies_token_with_auth_service(supa_store):
    from supabase_auth.errors import AuthApiError

    identity = SimpleNamespace(id=str(uuid.uuid4()))
    seen = []

    def get_user(token):
        seen.append(token)
        if token == "expired":
            raise AuthApiError("token expired", 401, None)
        return SimpleNamespace(user=identity)

    supa_store._client.auth = SimpleNamespace(get_user=get_user)
    assert run(supa_store.authenticate("signed-token")) is identity
    assert run(supa_store.authenticate("expired")) is None
    assert seen == ["signed-token", "expired"]

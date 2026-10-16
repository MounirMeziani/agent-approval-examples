"""Crash tests for LangGraph 1.2.15 interrupt() approvals. Offline, no LLM."""
import json, os, subprocess, sys
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import ledger
from approval import build, saver

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approval.py")
CFG = {"configurable": {"thread_id": "order-42"}}

def cli(*args):
    r = subprocess.run([sys.executable, APP, *args], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])

def test_approve(tmp_path):
    db = str(tmp_path / "l.db"); g = build(InMemorySaver(), db)
    out = g.invoke({}, CFG)
    assert out["__interrupt__"][0].value["amount"] == 1999 and ledger.effects(db) == []
    out = g.invoke(Command(resume={"approved": True}), CFG)
    assert out["result"] == "charged" and len(ledger.effects(db)) == 1

def test_reject(tmp_path):
    db = str(tmp_path / "l.db"); g = build(InMemorySaver(), db)
    g.invoke({}, CFG)
    out = g.invoke(Command(resume={"approved": False, "reason": "no"}), CFG)
    assert out["approved"] is False and "result" not in out and ledger.effects(db) == []

def test_save_and_resume(tmp_path):
    db, ck = str(tmp_path / "l.db"), str(tmp_path / "ck.db")
    build(saver(ck), db).invoke({}, CFG)
    g2 = build(saver(ck), db)                               # fresh graph + fresh connection
    assert g2.get_state(CFG).next == ("approval",)
    assert g2.invoke(Command(resume={"approved": True}), CFG)["result"] == "charged"
    assert len(ledger.effects(db)) == 1

def test_restart_mid_approval(tmp_path):
    ck, db = str(tmp_path / "ck.db"), str(tmp_path / "l.db")
    assert cli("start", "--db", ck, "--ledger", db)["next"] == ["approval"]
    out = cli("resume", "--db", ck, "--ledger", db, "--decision", "approve")   # new process
    assert out["result"] == "charged" and out["next"] == [] and len(ledger.effects(db)) == 1

def test_replayed_execute_is_idempotent(tmp_path):
    db = str(tmp_path / "l.db"); g = build(InMemorySaver(), db)
    g.invoke({}, CFG); g.invoke(Command(resume={"approved": True}), CFG)
    before = next(h for h in g.get_state_history(CFG) if h.next == ("execute",))
    g.invoke(None, before.config)                           # at-least-once retry of execute
    assert len(ledger.effects(db)) == 1

def _two_workers(tmp_path, *extra):
    ck, db = str(tmp_path / "ck.db"), str(tmp_path / "l.db")
    cli("start", "--db", ck, "--ledger", db)
    procs = [subprocess.Popen([sys.executable, APP, "resume", "--db", ck, "--ledger", db, "--worker", w, "--hold", "2", *extra],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for w in ("w1", "w2")]
    for p in procs:
        _, err = p.communicate(timeout=300); assert p.returncode == 0, err[-2000:]
    return ledger.effects(db)

def test_multiple_workers_one_side_effect(tmp_path):
    rows = _two_workers(tmp_path)
    assert len(rows) == 1 and rows[0][0] == "charge:order-42"

def test_multiple_workers_without_key_double_charges(tmp_path):
    assert len(_two_workers(tmp_path, "--naive")) == 2

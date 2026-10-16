"""Crash tests for OpenAI Agents SDK 0.24.0 approvals. Offline: the model is scripted."""
import asyncio, json, os, subprocess, sys
import ledger
from approval import Ctx, start, resume

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approval.py")

def cli(*args):
    r = subprocess.run([sys.executable, APP, *args], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])

def test_approve(tmp_path):
    db = str(tmp_path / "l.db"); ctx = Ctx("order-42", db)
    r, saved = asyncio.run(start(ctx))
    assert [i.tool_name for i in r.interruptions] == ["charge"]
    assert ledger.effects(db) == []                          # nothing runs before approval
    out = asyncio.run(resume(saved, ctx, "approve"))
    assert out.interruptions == [] and "charged 1999" in out.final_output
    assert len(ledger.effects(db)) == 1

def test_reject(tmp_path):
    db = str(tmp_path / "l.db"); ctx = Ctx("order-42", db)
    _, saved = asyncio.run(start(ctx))
    out = asyncio.run(resume(saved, ctx, "reject"))
    assert "Human declined the charge." in out.final_output
    assert ledger.effects(db) == []

def test_save_and_resume(tmp_path):
    db = str(tmp_path / "l.db"); ctx = Ctx("order-42", db)
    _, saved = asyncio.run(start(ctx))
    path = tmp_path / "run.json"; path.write_text(saved)      # durable checkpoint
    json.loads(path.read_text())                             # plain JSON, storable anywhere
    out = asyncio.run(resume(path.read_text(), Ctx("order-42", db), "approve"))
    assert out.interruptions == [] and len(ledger.effects(db)) == 1

def test_restart_mid_approval(tmp_path):
    st, db = str(tmp_path / "run.json"), str(tmp_path / "l.db")
    assert cli("start", "--state", st, "--ledger", db)["interruptions"] == ["charge"]
    # process 1 has exited; a brand-new process restores the serialized state
    out = cli("resume", "--state", st, "--ledger", db, "--decision", "approve")
    assert out["interruptions"] == 0 and "charged 1999" in out["final"]
    assert len(ledger.effects(db)) == 1

def _two_workers(tmp_path, *extra):
    st, db = str(tmp_path / "run.json"), str(tmp_path / "l.db")
    cli("start", "--state", st, "--ledger", db)
    procs = [subprocess.Popen([sys.executable, APP, "resume", "--state", st, "--ledger", db,
                               "--worker", w, *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for w in ("w1", "w2")]
    for p in procs:
        _, err = p.communicate(timeout=300); assert p.returncode == 0, err[-2000:]
    return ledger.effects(db)

def test_multiple_workers_one_side_effect(tmp_path):
    rows = _two_workers(tmp_path)
    assert len(rows) == 1 and rows[0][0] == "order-42:charge"

def test_multiple_workers_without_key_double_charges(tmp_path):
    # Control: the SDK alone does not stop two workers resuming the same snapshot.
    assert len(_two_workers(tmp_path, "--naive")) == 2

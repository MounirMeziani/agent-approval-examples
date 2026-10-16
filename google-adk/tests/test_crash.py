"""Crash tests for Google ADK 2.12.0 tool confirmation. Offline: the LLM is scripted."""
import asyncio, json, os, subprocess, sys
import ledger, approval

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "approval.py")

def cli(*args):
    r = subprocess.run([sys.executable, APP, *args], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])

def setup(tmp_path):
    db, lg = str(tmp_path / "s.db"), str(tmp_path / "l.db")
    approval.CONF.update(ledger=lg, worker="main", idempotent=True)
    return db, lg

def charged(seen):
    return [r for n, r in seen["responses"] if n == "charge"]

def test_approve(tmp_path):
    db, lg = setup(tmp_path)
    s = asyncio.run(approval.start(db, "order-42"))
    assert s["confirmation"] and ledger.effects(lg) == []    # paused, nothing ran
    r = asyncio.run(approval.resume(db, "order-42", "approve"))
    assert charged(r)[0]["status"] == "charged" and len(ledger.effects(lg)) == 1

def test_reject(tmp_path):
    db, lg = setup(tmp_path)
    asyncio.run(approval.start(db, "order-42"))
    r = asyncio.run(approval.resume(db, "order-42", "reject"))
    assert "error" in json.dumps(charged(r)).lower() or "reject" in json.dumps(charged(r)).lower()
    assert ledger.effects(lg) == []

def test_save_and_resume(tmp_path):
    db, lg = setup(tmp_path)
    asyncio.run(approval.start(db, "order-42"))
    runner = approval.make_runner(db)                        # new runner reads persisted session
    assert asyncio.run(approval.pending_confirmation(runner, "order-42"))
    asyncio.run(approval.resume(db, "order-42", "approve"))
    assert len(ledger.effects(lg)) == 1
    assert asyncio.run(approval.pending_confirmation(approval.make_runner(db), "order-42")) is None

def test_restart_mid_approval(tmp_path):
    db, lg = str(tmp_path / "s.db"), str(tmp_path / "l.db")
    assert cli("start", "--db", db, "--ledger", lg)["confirmation"]
    out = cli("resume", "--db", db, "--ledger", lg, "--decision", "approve")   # new process
    assert charged(out)[0]["status"] == "charged" and len(ledger.effects(lg)) == 1

def _two_workers(tmp_path, *extra):
    db, lg = str(tmp_path / "s.db"), str(tmp_path / "l.db")
    cli("start", "--db", db, "--ledger", lg)
    procs = [subprocess.Popen([sys.executable, APP, "resume", "--db", db, "--ledger", lg, "--worker", w, *extra],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for w in ("w1", "w2")]
    outs = []
    for p in procs:
        o, err = p.communicate(timeout=300); assert p.returncode == 0, err[-2000:]
        outs.append(json.loads(o.strip().splitlines()[-1]))
    return ledger.effects(lg), outs

def test_multiple_workers_one_side_effect(tmp_path):
    # The losing worker may get StaleSessionError from ADK's optimistic session check, possibly
    # after its tool already ran; the idempotency key keeps the side effect at exactly one.
    rows, outs = _two_workers(tmp_path)
    assert len(rows) == 1 and rows[0][0].startswith("order-42:")

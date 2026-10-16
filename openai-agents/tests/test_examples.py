"""Ported template: naive vs idempotent tool across duplicate resume and whole-run retry."""
import os, re, subprocess, sys
EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
def n(s): return len(re.findall(r"\('ch_\d+', \d+\)", s))
def test_idempotent_template():
    r = subprocess.run([sys.executable, "idempotent_approval.py"], cwd=EX, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    naive, fixed = r.stdout.split("== FIXED / A)")
    assert "paused: ['charge_fixed'], charges=0" in fixed
    assert n(re.search(r"after 2 resumes of same snapshot: (.*)", fixed).group(1)) == 1
    assert n(re.search(r"final charges=(.*)", fixed).group(1)) == 1     # retry after crash: once
    assert n(re.search(r"after 2 resumes of same snapshot: (.*)", naive).group(1)) == 2

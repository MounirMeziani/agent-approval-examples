// Crash tests for Vercel AI SDK ai@7.0.142 toolApproval. Offline: the model is mocked.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { start, resume } from '../approval.mjs';
import { effects } from '../ledger.mjs';

const APP = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'approval.mjs');
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'aiapr-'));
const cli = (...args) => JSON.parse(execFileSync('node', [APP, ...args], { encoding: 'utf8' }).trim().split('\n').pop());

test('approve', async () => {
  const ledger = path.join(tmp(), 'fx'); const cfg = { ledger };
  const { saved, requests } = await start(cfg);
  assert.deepEqual(requests.map((q) => q.toolCall.toolName), ['refund']);
  assert.equal(effects(ledger).length, 0);                       // nothing before approval
  const r = await resume(saved, cfg, 'approve');
  assert.match(r.text, /refunded/); assert.equal(effects(ledger).length, 1);
});

test('reject', async () => {
  const ledger = path.join(tmp(), 'fx'); const cfg = { ledger };
  const { saved } = await start(cfg);
  const r = await resume(saved, cfg, 'reject');
  assert.ok(r.types.includes('tool-output-denied') || /denied|declined/i.test(JSON.stringify(r)), JSON.stringify(r));
  assert.equal(effects(ledger).length, 0);
});

test('save and resume', async () => {
  const dir = tmp(); const ledger = path.join(dir, 'fx');
  const { saved } = await start({ ledger });
  fs.writeFileSync(path.join(dir, 'run.json'), saved);           // plain JSON checkpoint
  await resume(fs.readFileSync(path.join(dir, 'run.json'), 'utf8'), { ledger }, 'approve');
  assert.equal(effects(ledger).length, 1);
});

test('restart mid-approval (new process resumes)', () => {
  const dir = tmp(); const st = path.join(dir, 'run.json'); const lg = path.join(dir, 'fx');
  assert.deepEqual(cli('start', '--state', st, '--ledger', lg).requests, ['refund']);
  const r = cli('resume', '--state', st, '--ledger', lg, '--decision', 'approve');
  assert.match(r.text, /refunded/); assert.equal(effects(lg).length, 1);
});

async function twoWorkers(...extra) {
  const dir = tmp(); const st = path.join(dir, 'run.json'); const lg = path.join(dir, 'fx');
  cli('start', '--state', st, '--ledger', lg);
  await Promise.all(['w1', 'w2'].map((w) => new Promise((res, rej) => {
    const p = spawn('node', [APP, 'resume', '--state', st, '--ledger', lg, '--worker', w, ...extra]);
    let err = ''; p.stderr.on('data', (d) => (err += d));
    p.on('exit', (c) => (c === 0 ? res() : rej(new Error(err))));
  })));
  return effects(lg);
}

test('multiple workers: one side effect with idempotency key', async () => {
  const rows = await twoWorkers();
  assert.equal(rows.length, 1); assert.equal(rows[0].key, 'order-42:call_refund');
});

test('control: multiple workers without key refund twice', async () => {
  assert.equal((await twoWorkers('--naive')).length, 2);
});

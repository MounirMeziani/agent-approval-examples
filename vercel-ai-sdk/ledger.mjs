// Side-effect ledger shared across processes (stands in for a payment API).
// With a key, the first caller atomically creates <dir>/<key> (O_EXCL); later callers are no-ops.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
export function perform(dir, action, key, worker = 'main') {
  fs.mkdirSync(dir, { recursive: true });
  const name = key ? 'k_' + crypto.createHash('sha256').update(key).digest('hex').slice(0, 16)
                   : 'n_' + crypto.randomUUID();
  try { fs.writeFileSync(path.join(dir, name), JSON.stringify({ key: key ?? null, action, worker }), { flag: 'wx' }); return true; }
  catch (e) { if (e.code === 'EEXIST') return false; throw e; }
}
export function effects(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')));
}

// Vercel AI SDK (ai@7.0.142): toolApproval (not the deprecated tool-level needsApproval) that
// survives restarts and duplicate resumes.
// Flow: generateText with toolApproval -> 'tool-approval-request' -> save messages as JSON ->
// any process appends a 'tool-approval-response' and calls generateText again.
// The refund side effect uses an idempotency key (order id + toolCallId), so two workers resuming
// the same saved messages refund once. MOCK: only the model (MockLanguageModelV4 from 'ai/test').
//
// CLI (used by the tests):
//   node approval.mjs start  --state run.json --ledger effects/
//   node approval.mjs resume --state run.json --ledger effects/ --decision approve|reject [--worker w1] [--naive]
import fs from 'node:fs';
import { parseArgs } from 'node:util';
import { generateText, tool, stepCountIs } from 'ai';
import { MockLanguageModelV4 } from 'ai/test';
import { z } from 'zod';
import { perform } from './ledger.mjs';

const usage = { inputTokens: { total: 1, noCache: 1, cacheRead: 0, cacheWrite: 0 }, outputTokens: { total: 1, text: 1, reasoning: 0 } };
const finish = (u) => ({ unified: u, raw: u });

export function makeModel() {
  return new MockLanguageModelV4({
    doGenerate: async ({ prompt }) => {
      const results = prompt.filter((m) => m.role === 'tool').flatMap((m) => m.content).filter((p) => p.type === 'tool-result');
      if (results.length) {
        return { content: [{ type: 'text', text: 'Model saw: ' + JSON.stringify(results.map((r) => r.output)) }],
                 finishReason: finish('stop'), usage, warnings: [] };
      }
      return { content: [{ type: 'tool-call', toolCallId: 'call_refund', toolName: 'refund',
                           input: JSON.stringify({ orderId: '42', amount: 1999 }) }],
               finishReason: finish('tool-calls'), usage, warnings: [] };
    },
  });
}

export function makeOpts({ ledger, worker = 'main', idempotent = true }) {
  const tools = {
    refund: tool({
      description: 'Refund an order',
      inputSchema: z.object({ orderId: z.string(), amount: z.number() }),
      execute: async ({ orderId, amount }, { toolCallId }) => {
        const ran = perform(ledger, `refund ${orderId} ${amount}`, idempotent ? `order-${orderId}:${toolCallId}` : null, worker);
        return { status: ran ? 'refunded' : 'already refunded', amount };
      },
    }),
  };
  const toolApproval = { refund: () => ({ type: 'user-approval', reason: 'refunds need a human' }) };
  return { model: makeModel(), tools, toolApproval, stopWhen: stepCountIs(5) };
}

export async function start(cfg) {
  const messages = [{ role: 'user', content: 'refund order 42' }];
  const r = await generateText({ ...makeOpts(cfg), messages });
  const requests = r.content.filter((p) => p.type === 'tool-approval-request' && !p.isAutomatic);
  return { saved: JSON.stringify([...messages, ...r.response.messages]), requests };
}

export function pendingApprovals(messages) {
  const asked = messages.filter((m) => m.role === 'assistant' && Array.isArray(m.content))
    .flatMap((m) => m.content).filter((p) => p.type === 'tool-approval-request');
  const answered = new Set(messages.filter((m) => m.role === 'tool').flatMap((m) => m.content)
    .filter((p) => p.type === 'tool-approval-response').map((p) => p.approvalId));
  return asked.filter((p) => !answered.has(p.approvalId));
}

export async function resume(saved, cfg, decision) {
  const messages = JSON.parse(saved);
  const pending = pendingApprovals(messages);
  if (!pending.length) throw new Error('no pending approval');
  messages.push({ role: 'tool', content: pending.map((p) => ({ type: 'tool-approval-response', approvalId: p.approvalId,
    approved: decision === 'approve', reason: decision === 'approve' ? 'ok' : 'Human declined the refund.' })) });
  const r = await generateText({ ...makeOpts(cfg), messages });
  return { text: r.text, types: r.content.map((p) => p.type) };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const { positionals: [cmd], values: a } = parseArgs({ allowPositionals: true, options: {
    state: { type: 'string' }, ledger: { type: 'string' }, decision: { type: 'string', default: 'approve' },
    worker: { type: 'string', default: 'main' }, naive: { type: 'boolean', default: false } } });
  const cfg = { ledger: a.ledger, worker: a.worker, idempotent: !a.naive };
  if (cmd === 'start') {
    const { saved, requests } = await start(cfg);
    fs.writeFileSync(a.state, saved);
    console.log(JSON.stringify({ requests: requests.map((q) => q.toolCall.toolName) }));
  } else {
    console.log(JSON.stringify(await resume(fs.readFileSync(a.state, 'utf8'), cfg, a.decision)));
  }
}

# agent-approval-examples

Human-approval (human-in-the-loop) examples for AI agent frameworks that survive a process restart and don't charge twice when two workers resume the same approval. Each framework folder has pinned versions and crash tests (pytest for Python, `node --test` for the Vercel AI SDK). The model is mocked or there's no LLM, so nothing needs an API key.

> **Status:** CI runs every suite in `.github/workflows/ci.yml` on each push. TODO: status badge once the repo is public.

## What each example proves

All examples use the same scenario: an agent wants to charge order 42 for `1999`. A human approves or rejects it. The charge is written to a SQLite "ledger" (`ledger.py`) that stands in for a payment API. The ledger has a `UNIQUE` idempotency key, so only the first caller with a given key actually runs the side effect. The Vercel AI SDK folder uses `ledger.mjs`, which does the same with one atomically created file per key.

### `openai-agents/`: OpenAI Agents SDK 0.24.0
- `approval.py`: a `@function_tool(needs_approval=True)` tool pauses the run. The state is saved with `RunState.to_string()` and restored in any process with `RunState.from_string()`, then approved or rejected and resumed with `Runner.run(agent, state)`. The idempotency key is `order_id:tool_name`. Model: `fake_model.ScriptedModel` (scripted, offline).
- `examples/idempotent_approval.py`: compares a naive tool with an idempotent tool in two failure cases: (A) the same saved run is resumed twice, and (B) the run crashes *after* the tool ran (a simulated model 503) and the app retries from scratch.

### `langgraph/`: LangGraph 1.2.15
- `approval.py`: `propose -> approval (interrupt()) -> execute`. Nothing has side effects before the interrupt. Checkpoints are stored with `SqliteSaver`, so a fresh process can resume. `execute` uses the idempotency key `charge:<thread_id>`. There's no LLM; `propose` is deterministic.

### `google-adk/`: Google ADK 2.12.0
- `approval.py`: `FunctionTool(charge, require_confirmation=True)` makes ADK emit `adk_request_confirmation`. The session is stored with `SqliteSessionService`, so a fresh process finds the pending confirmation and answers it with a `ToolConfirmation` function response. The idempotency key is `<session_id>:<function_call_id>`. Model: `ScriptedLlm` (a `BaseLlm` subclass, offline).
- Why the idempotency key is required: when two workers answer the same confirmation at once, ADK raises `StaleSessionError` on the slower worker, but only *after* its tool may already have run. ADK's session check rejects the duplicate event; it does not stop the side effect. In a manual run without the key, both workers charged (2 ledger rows). With the key, the second charge is a no-op, so `test_multiple_workers_one_side_effect` always sees exactly one.

### `vercel-ai-sdk/`: Vercel AI SDK `ai@7.0.142`
- `approval.mjs`: approval is configured with `toolApproval` on `generateText` (the tool-level `needsApproval` is deprecated in v7). The run returns a `tool-approval-request`; the messages are saved as JSON, and any process appends a `tool-approval-response` and calls `generateText` again. The idempotency key is `order-42:<toolCallId>`. Model: `MockLanguageModelV4` from `ai/test`.

## Pinned versions

Each folder's `requirements.txt` pins only the packages its example and tests import directly; pip resolves the rest:

| Folder | Framework | Other key pins |
|---|---|---|
| `openai-agents/` | `openai-agents==0.24.0` | `openai==3.31.0`, `pydantic==2.14.1`, `pytest==9.1.1` |
| `langgraph/` | `langgraph==1.2.15` | `langgraph-checkpoint==4.3.0`, `langgraph-checkpoint-sqlite==3.1.2`, `pytest==9.1.1` |
| `google-adk/` | `google-adk==2.12.0` | `google-genai==2.31.0`, `aiosqlite==0.22.1`, `pytest==9.1.1` |
| `vercel-ai-sdk/` | `ai@7.0.142` | `zod@4.6.7`, full tree in `package-lock.json` |

CI pins Python 3.13.5 and Node.js 20.19.2.

## Failure modes the tests cover

| Failure mode | OpenAI Agents SDK (`openai-agents/tests/`) | LangGraph (`langgraph/tests/`) | Google ADK (`google-adk/tests/`) | Vercel AI SDK (`vercel-ai-sdk/test/`) |
|---|---|---|---|---|
| Approve: nothing runs before approval, then exactly one side effect | `test_approve` | `test_approve` | `test_approve` | `approve` |
| Reject: no side effect (OpenAI: the rejection message reaches the final output) | `test_reject` | `test_reject` | `test_reject` | `reject` |
| Save the state, then resume from a fresh object or connection | `test_save_and_resume` (state is plain JSON written to a file) | `test_save_and_resume` (new graph and new SQLite connection) | `test_save_and_resume` (new runner reads the SQLite session) | `save and resume` (messages as JSON) |
| Restart mid-approval: start in one process, resume in a brand-new process | `test_restart_mid_approval` | `test_restart_mid_approval` | `test_restart_mid_approval` | `restart mid-approval (new process resumes)` |
| Two workers resume the same approval at once, with an idempotency key, so there's exactly one side effect | `test_multiple_workers_one_side_effect` | `test_multiple_workers_one_side_effect` | `test_multiple_workers_one_side_effect` | `multiple workers: one side effect with idempotency key` |
| Control: the same race *without* the key causes a double charge (2 side effects) | `test_multiple_workers_without_key_double_charges` | `test_multiple_workers_without_key_double_charges` | n/a (whether both workers overlap is timing-dependent in ADK) | `control: multiple workers without key refund twice` |
| A replayed `execute` step (at-least-once retry from checkpoint history) still has one side effect | n/a | `test_replayed_execute_is_idempotent` | n/a | n/a |
| Duplicate resume of the same snapshot: naive = 2 charges, idempotent = 1. Retry after a crash post-tool: idempotent = 1 | `test_examples.py::test_idempotent_template` | n/a | n/a | n/a |

What the control tests show: none of these frameworks stops two workers from resuming the same saved approval and running the tool twice. That protection comes from the idempotency key on the side effect.

## Run it

```bash
git clone https://github.com/MounirMeziani/agent-approval-examples   # TODO: confirm URL once the repo is public
cd agent-approval-examples

# OpenAI Agents SDK
python -m venv .venv-openai && . .venv-openai/bin/activate
pip install -r openai-agents/requirements.txt
cd openai-agents && pytest tests -q && cd ..
deactivate

# LangGraph
python -m venv .venv-langgraph && . .venv-langgraph/bin/activate
pip install -r langgraph/requirements.txt
cd langgraph && pytest tests -q && cd ..
deactivate

# Google ADK
python -m venv .venv-adk && . .venv-adk/bin/activate
pip install -r google-adk/requirements.txt
cd google-adk && pytest tests -q && cd ..
deactivate

# Vercel AI SDK (Node.js 20)
cd vercel-ai-sdk && npm ci && npm test && cd ..
```

The frameworks pull in different transitive versions, so use one virtualenv per folder.

To try the approval flow by hand (OpenAI Agents SDK):

```bash
cd openai-agents
python approval.py start  --state run.json --ledger effects.db
python approval.py resume --state run.json --ledger effects.db --decision approve   # or reject
```

LangGraph:

```bash
cd langgraph
python approval.py start  --db ckpt.db --ledger effects.db
python approval.py resume --db ckpt.db --ledger effects.db --decision approve   # thread defaults to order-42
```

Google ADK:

```bash
cd google-adk
python approval.py start  --db sessions.db --ledger effects.db
python approval.py resume --db sessions.db --ledger effects.db --decision approve
```

Vercel AI SDK:

```bash
cd vercel-ai-sdk
node approval.mjs start  --state run.json --ledger effects
node approval.mjs resume --state run.json --ledger effects --decision approve
```

Duplicate-resume / retry demo:

```bash
cd openai-agents/examples && python idempotent_approval.py
```

## Not covered (yet)

- Real model calls. Every test is offline.
- OpenAI Agents SDK (JS), CrewAI: TODO.

## More

Tested agent-framework changes and failure modes like these are written up weekly at [Agent Signal](https://tryagentsignal.com).

## License

MIT. See [LICENSE](LICENSE).

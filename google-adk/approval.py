"""Google ADK 2.12.0: tool confirmation that survives restarts and duplicate resumes.

Flow: FunctionTool(require_confirmation=...) -> ADK emits adk_request_confirmation -> the session
is persisted by SqliteSessionService -> any process answers with a ToolConfirmation function
response -> the tool runs (or not). The side effect uses an idempotency key (session id + the
original function call id), so two workers answering the same confirmation charge once.
MOCK: only the LLM (ScriptedLlm subclasses google.adk.models.BaseLlm).

CLI (used by the crash tests):
  python approval.py start  --db sessions.db --ledger effects.db --session order-42
  python approval.py resume --db sessions.db --ledger effects.db --session order-42 --decision approve|reject [--worker w1] [--naive]
"""
import argparse, asyncio, json
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.tools import FunctionTool, ToolContext
from google.adk.tools.tool_confirmation import ToolConfirmation
from google.genai import types
from google.adk.errors._stale_session_error import StaleSessionError
import ledger

APP, USER = "billing", "u"
CONF = {"ledger": None, "worker": "main", "idempotent": True}

class ScriptedLlm(BaseLlm):
    model: str = "scripted-fake"
    async def generate_content_async(self, llm_request, stream=False):
        last = llm_request.contents[-1]
        fr = [p.function_response for p in last.parts if p.function_response and p.function_response.name == "charge"]
        if fr:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=f"Tool result: {fr[0].response}")]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(
                function_call=types.FunctionCall(name="charge", args={"amount": 1999}))]))

def charge(amount: int, tool_context: ToolContext) -> dict:
    """Charge the customer."""
    key = f"{tool_context.session.id}:{tool_context.function_call_id}" if CONF["idempotent"] else None
    ran = ledger.perform(CONF["ledger"], f"charge {amount}", key=key, worker=CONF["worker"])
    return {"status": "charged" if ran else "already charged", "amount": amount}

def make_runner(db):
    agent = LlmAgent(name="bank", model=ScriptedLlm(), instruction="bill",
                     tools=[FunctionTool(charge, require_confirmation=True)])
    return Runner(agent=agent, app_name=APP, session_service=SqliteSessionService(db))

async def _turn(runner, sid, msg):
    seen = {"confirmation": None, "responses": [], "text": []}
    async for ev in runner.run_async(user_id=USER, session_id=sid, new_message=msg):
        for p in (ev.content.parts if ev.content else []):
            if p.function_call and p.function_call.name == "adk_request_confirmation":
                seen["confirmation"] = p.function_call.id
            elif p.function_response:
                seen["responses"].append([p.function_response.name, p.function_response.response])
            elif p.text:
                seen["text"].append(p.text)
    return seen

async def start(db, sid):
    runner = make_runner(db)
    await runner.session_service.create_session(app_name=APP, user_id=USER, session_id=sid)
    return await _turn(runner, sid, types.Content(role="user", parts=[types.Part(text="charge order 42")]))

async def pending_confirmation(runner, sid):
    """Find the unanswered adk_request_confirmation call in the persisted session."""
    s = await runner.session_service.get_session(app_name=APP, user_id=USER, session_id=sid)
    asked, answered = None, set()
    for ev in s.events:
        for p in (ev.content.parts if ev.content else []):
            if p.function_call and p.function_call.name == "adk_request_confirmation": asked = p.function_call.id
            if p.function_response and p.function_response.name == "adk_request_confirmation": answered.add(p.function_response.id)
    return asked if asked not in answered else None

async def resume(db, sid, decision):
    runner = make_runner(db)                          # fresh runner + fresh DB connection
    fc_id = await pending_confirmation(runner, sid)
    assert fc_id, "no pending confirmation in session"
    msg = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
        id=fc_id, name="adk_request_confirmation",
        response=ToolConfirmation(confirmed=decision == "approve").model_dump()))])
    return await _turn(runner, sid, msg)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["start", "resume"])
    p.add_argument("--db", required=True); p.add_argument("--ledger", required=True)
    p.add_argument("--session", default="order-42"); p.add_argument("--decision", default="approve")
    p.add_argument("--worker", default="main"); p.add_argument("--naive", action="store_true")
    a = p.parse_args()
    CONF.update(ledger=a.ledger, worker=a.worker, idempotent=not a.naive)
    try:
        out = asyncio.run(start(a.db, a.session) if a.cmd == "start" else resume(a.db, a.session, a.decision))
    except StaleSessionError as e:  # another worker wrote the session first (ADK optimistic concurrency)
        out = {"stale_session": True, "error": str(e)[:200]}
    print(json.dumps(out, default=str))

if __name__ == "__main__":
    main()

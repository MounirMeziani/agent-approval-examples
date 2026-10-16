"""Offline scripted Model (MOCK). Replaces the OpenAI API; everything else is real SDK code."""
from __future__ import annotations
import json
from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

class ScriptedModel(Model):
    """First turn: emit the given tool calls. Later turns: echo tool outputs as final text."""
    def __init__(self, calls: list[tuple[str, dict]]):
        self.calls = calls
        self.turn = 0

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema,
                           handoffs, tracing, *, previous_response_id=None, conversation_id=None, prompt=None, **kw):
        self.turn += 1
        items = input if isinstance(input, list) else []
        outputs = [i for i in items if isinstance(i, dict) and i.get("type") == "function_call_output"]
        if not outputs:
            out = [ResponseFunctionToolCall(type="function_call", id=f"fc_{n}", call_id=f"call_{n}",
                                            name=name, arguments=json.dumps(args))
                   for n, (name, args) in enumerate(self.calls)]
        else:
            text = "Model saw tool outputs: " + " | ".join(f"{o['call_id']}={o['output']}" for o in outputs)
            out = [ResponseOutputMessage(type="message", id="msg_1", role="assistant", status="completed",
                   content=[ResponseOutputText(type="output_text", text=text, annotations=[])])]
        return ModelResponse(output=out, usage=Usage(), response_id=None)

    def stream_response(self, *a, **kw):
        raise NotImplementedError("streaming not mocked")

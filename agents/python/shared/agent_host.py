"""Lightweight A2A-compatible host for specialist agents.

Two execution paths live here, switchable via the ``MAF_NATIVE_EXECUTION``
env flag (plans/refactor/03-retire-agent-host-custom-loop.md):

- When ``MAF_NATIVE_EXECUTION=true`` (default, Phase 7 step 03+):
  ``create_agent_app`` drives each request through MAF's native
  ``agent.run()`` / ``agent.run(..., stream=True)`` — the canonical
  code path that the tutorial series teaches. Tool definitions and
  system prompts are owned by the ``Agent`` object itself.

- When ``MAF_NATIVE_EXECUTION=false``: the legacy custom
  ``_run_agent_with_tools`` / ``_run_agent_with_tools_stream``
  functions run instead. They use the OpenAI chat-completions API
  directly to dodge an older Azure API-version incompatibility. Kept
  as a rollback escape hatch behind the flag; will be removed once
  production deployments are settled on the native path.

Both paths produce identical observable output on the ``/message:send``
HTTP contract, so the flag can be flipped per-environment without any
code change on the consumer side.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─────────────────────── MAF-native execution ───────────────────────


def _history_as_maf_messages(history: list[dict] | None, user_message: str) -> list[Any]:
    """Convert the A2A history payload + current user message into MAF
    :class:`agent_framework.Message` objects.

    The legacy code path kept history as raw OpenAI-style dicts and stitched
    them into the prompt by hand. The native path hands the full list to
    ``agent.run`` which is responsible for threading it through the
    conversation.
    """
    from agent_framework import Message

    messages: list[Any] = []
    if history:
        for entry in history:
            role = entry.get("role")
            content = entry.get("content")
            if role in ("user", "assistant") and content:
                messages.append(Message(role=role, contents=[content]))
    messages.append(Message(role="user", contents=[user_message]))
    return messages


async def _run_agent_native(agent: Any, user_message: str, history: list[dict] | None = None) -> str:
    """Run an agent via MAF's native execution path and return the answer text."""
    messages = _history_as_maf_messages(history, user_message)
    response = await agent.run(messages)
    return response.text or ""


async def _run_agent_native_stream(
    agent: Any,
    user_message: str,
    history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """Streaming variant — yields the same text chunks the legacy loop produced."""
    messages = _history_as_maf_messages(history, user_message)
    async for update in agent.run(messages, stream=True):
        text = getattr(update, "text", None)
        if text:
            yield text


async def _run_agent_with_tools(
    system_prompt: str,
    tools: list[Callable],
    user_message: str,
    history: list[dict] | None = None,
    user_context: str | None = None,
) -> str:
    """Run a tool-calling loop using the OpenAI chat completions API directly.

    Args:
        system_prompt: The agent's system instructions.
        tools: List of MAF FunctionTool objects.
        user_message: The current user message.
        history: Previous conversation messages [{"role": ..., "content": ...}].
        user_context: Extra context about the current user (injected into system prompt).
    """
    import openai

    if settings.LLM_PROVIDER == "azure":
        client = openai.AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        model = settings.AZURE_OPENAI_DEPLOYMENT
    else:
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        model = settings.LLM_MODEL

    # Build tool definitions from MAF FunctionTool objects
    tool_defs = []
    tool_map = {}
    for t in tools:
        # MAF @tool decorator returns FunctionTool with .name, .description, .to_json_schema_spec()
        name = getattr(t, "name", None) or getattr(t, "__name__", str(t))
        desc = getattr(t, "description", None) or getattr(t, "__doc__", "") or ""

        # Get the full JSON schema from MAF FunctionTool
        try:
            schema = t.to_json_schema_spec()
            # Schema is {"type": "function", "function": {"name": ..., "parameters": ...}}
            func_schema = schema.get("function", schema)
            params = func_schema.get("parameters", {"type": "object", "properties": {}})
        except Exception:
            params = {"type": "object", "properties": {}}

        tool_defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc[:1024],
                "parameters": params,
            },
        })
        # Map name to the FunctionTool's invoke method or the tool itself
        tool_map[name] = t

    # Build system prompt with user context
    full_system = system_prompt
    if user_context:
        full_system += f"\n\n## Current User Context\n{user_context}"

    messages: list[dict] = [{"role": "system", "content": full_system}]

    # Add conversation history (excluding system messages)
    if history:
        for h in history:
            if h.get("role") in ("user", "assistant"):
                messages.append({"role": h["role"], "content": h["content"]})

    # Add current user message
    messages.append({"role": "user", "content": user_message})

    # Tool-calling loop (max 5 iterations)
    for _ in range(5):
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.1}
        if tool_defs:
            kwargs["tools"] = tool_defs
            kwargs["tool_choice"] = "auto"

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
            messages.append(choice.message.model_dump())
            from shared.telemetry import tool_call_span
            for tc in choice.message.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                tool_fn = tool_map.get(fn_name)
                if tool_fn:
                    with tool_call_span(fn_name) as tspan:
                        try:
                            # FunctionTool has .func for the raw async function
                            raw_fn = getattr(tool_fn, "func", tool_fn)
                            if callable(raw_fn):
                                result = await raw_fn(**fn_args)
                            else:
                                result = await tool_fn.invoke(**fn_args)
                            result_str = json.dumps(result, default=str) if not isinstance(result, str) else result
                            if tspan:
                                tspan.set_attribute("tool.result.length", len(result_str))
                            logger.info("tool.result name=%s len=%d", fn_name, len(result_str))
                        except Exception as e:
                            logger.exception("tool.error name=%s", fn_name)
                            result_str = json.dumps({"error": str(e)})
                else:
                    result_str = json.dumps({"error": f"Unknown tool: {fn_name}"})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
            continue

        # Final response (no tool calls)
        return choice.message.content or ""

    return "I processed your request but reached the maximum number of steps."


async def _run_agent_with_tools_stream(
    system_prompt: str,
    tools: list[Callable],
    user_message: str,
    history: list[dict] | None = None,
    user_context: str | None = None,
) -> AsyncGenerator[str, None]:
    """Streaming variant of _run_agent_with_tools.

    Yields text chunks as they arrive from the LLM. When the model invokes
    tools mid-stream, the generator accumulates the tool calls, executes
    them, appends the results, and re-enters the streaming loop so the
    next LLM turn is also streamed.

    Args:
        system_prompt: The agent's system instructions.
        tools: List of MAF FunctionTool objects.
        user_message: The current user message.
        history: Previous conversation messages [{"role": ..., "content": ...}].
        user_context: Extra context about the current user (injected into system prompt).
    """
    import openai

    if settings.LLM_PROVIDER == "azure":
        client = openai.AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        model = settings.AZURE_OPENAI_DEPLOYMENT
    else:
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        model = settings.LLM_MODEL

    # Build tool definitions from MAF FunctionTool objects
    tool_defs: list[dict] = []
    tool_map: dict[str, Any] = {}
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", str(t))
        desc = getattr(t, "description", None) or getattr(t, "__doc__", "") or ""
        try:
            schema = t.to_json_schema_spec()
            func_schema = schema.get("function", schema)
            params = func_schema.get("parameters", {"type": "object", "properties": {}})
        except Exception:
            params = {"type": "object", "properties": {}}

        tool_defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc[:1024],
                "parameters": params,
            },
        })
        tool_map[name] = t

    # Build system prompt with user context
    full_system = system_prompt
    if user_context:
        full_system += f"\n\n## Current User Context\n{user_context}"

    messages: list[dict] = [{"role": "system", "content": full_system}]
    if history:
        for h in history:
            if h.get("role") in ("user", "assistant"):
                messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    # Streaming tool-calling loop (max 5 iterations for tool rounds)
    for _ in range(5):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "stream": True,
        }
        if tool_defs:
            kwargs["tools"] = tool_defs
            kwargs["tool_choice"] = "auto"

        # Accumulators for the streamed response
        content_chunks: list[str] = []
        tool_calls_by_index: dict[int, dict] = {}

        stream = await client.chat.completions.create(**kwargs)

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            # --- Text content ---
            if delta.content:
                content_chunks.append(delta.content)
                yield delta.content

            # --- Tool calls (accumulated across deltas) ---
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_by_index:
                        tool_calls_by_index[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    entry = tool_calls_by_index[idx]
                    if tc_delta.id:
                        entry["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            entry["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            entry["arguments"] += tc_delta.function.arguments

            # Check for stream end
            if chunk.choices[0].finish_reason is not None:
                break

        # If no tool calls were accumulated, the response is complete
        if not tool_calls_by_index:
            return

        # Execute accumulated tool calls and feed results back
        assistant_tool_calls = []
        for idx in sorted(tool_calls_by_index):
            tc = tool_calls_by_index[idx]
            assistant_tool_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": tc["arguments"]},
            })

        messages.append({
            "role": "assistant",
            "content": "".join(content_chunks) or None,
            "tool_calls": assistant_tool_calls,
        })

        from shared.telemetry import tool_call_span
        for tc in assistant_tool_calls:
            fn_name = tc["function"]["name"]
            fn_args_str = tc["function"]["arguments"]
            fn_args = json.loads(fn_args_str) if fn_args_str else {}
            tool_fn = tool_map.get(fn_name)

            if tool_fn:
                with tool_call_span(fn_name) as tspan:
                    try:
                        raw_fn = getattr(tool_fn, "func", tool_fn)
                        if callable(raw_fn):
                            result = await raw_fn(**fn_args)
                        else:
                            result = await tool_fn.invoke(**fn_args)
                        result_str = json.dumps(result, default=str) if not isinstance(result, str) else result
                        if tspan:
                            tspan.set_attribute("tool.result.length", len(result_str))
                        logger.info("tool.result name=%s len=%d", fn_name, len(result_str))
                    except Exception as e:
                        logger.exception("tool.error name=%s", fn_name)
                        result_str = json.dumps({"error": str(e)})
            else:
                result_str = json.dumps({"error": f"Unknown tool: {fn_name}"})

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})

        # Reset content accumulator for the next LLM turn
        content_chunks = []
        # Continue the loop — the next iteration will stream the post-tool response

    yield "I processed your request but reached the maximum number of steps."


def create_agent_app(
    *,
    agent: Any,
    agent_name: str,
    port: int,
    description: str = "",
    tools: list | None = None,
    on_startup: Callable | None = None,
    on_shutdown: Callable | None = None,
) -> FastAPI:
    """Create a FastAPI app that hosts an agent with A2A-compatible endpoints.

    Args:
        agent: The MAF Agent instance (used for metadata only now)
        agent_name: Agent identifier matching the YAML config filename
        port: Port number
        tools: List of MAF FunctionTool objects for the tool-calling loop
        on_startup/on_shutdown: Lifecycle callbacks
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if on_startup:
            await on_startup(app)
        logger.info("%s.started port=%d", agent_name, port)
        yield
        if on_shutdown:
            await on_shutdown()

    app = FastAPI(title=agent_name, lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "agent": agent_name, "port": port}

    @app.get("/.well-known/agent-card.json")
    async def agent_card():
        return {
            "name": agent_name,
            "description": description,
            "url": f"http://{agent_name}:{port}",
            "version": "1.0",
        }

    @app.post("/message:send")
    async def message_send(request: Request):
        try:
            body = await request.json()
            message = body.get("message", "")
            if not message:
                return JSONResponse({"error": "No message provided"}, status_code=400)

            # Conversation history forwarded by orchestrator
            history = body.get("history", None)

            # Load role-aware prompt from YAML config
            user_email = request.headers.get("x-user-email", "")
            user_role = request.headers.get("x-user-role", "customer")
            try:
                from shared.prompt_loader import load_prompt
                system_prompt = load_prompt(agent_name, user_role)
            except Exception:
                system_prompt = getattr(agent, "_instructions", "") or getattr(agent, "instructions", "") or ""

            user_context = f"Current user email: {user_email}" if user_email else None

            # Use the tools list passed to create_agent_app
            agent_tools = tools or []

            from shared.telemetry import agent_run_span
            with agent_run_span(agent_name):
                if settings.MAF_NATIVE_EXECUTION:
                    # New path: agent already owns its tools/instructions/providers.
                    # History is threaded through MAF Message objects; user context
                    # is injected by the ContextProvider chain registered on the
                    # Agent (see shared/context_providers.py).
                    response_text = await _run_agent_native(agent, message, history=history)
                else:
                    # Legacy path — custom OpenAI chat-completions loop.
                    response_text = await _run_agent_with_tools(
                        system_prompt, agent_tools, message,
                        history=history,
                        user_context=user_context,
                    )
            return {"response": response_text}

        except Exception:
            logger.exception("%s.message_error", agent_name)
            return JSONResponse(
                {"error": "Agent processing failed"},
                status_code=500,
            )

    return app

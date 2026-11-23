from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from ..config import LANGGRAPH_CHECKPOINT_PATH
from .analysis import analyze_room
from .agent import answer_room_prompt
from .diagram import generate_mermaid_diagram
from .voting import propose_vote

AgentMode = Literal["conversation", "research", "analysis", "vote", "diagram"]


class AgentTurn(TypedDict):
    prompt: str
    response: str
    route: str


class RoomAgentState(TypedDict, total=False):
    current_prompt: str
    intent: str
    turns: list[AgentTurn]
    last_route: str


@dataclass
class RoomAgentRequest:
    session_id: str
    topic: str
    history: list[dict]
    prompt: str
    model: str
    phase: str
    summary: str | None
    structured_context: str | None
    member_names: list[str]
    external_sources: list[dict]
    mode: AgentMode
    force_analysis: bool
    auto_guidance: bool
    on_delta: Callable[[str], Awaitable[None] | None] | None
    diagram_type: str = "flowchart"
    output: str | dict[str, Any] | None = None


_request_context: ContextVar[RoomAgentRequest | None] = ContextVar("roomai_agent_request", default=None)
_checkpoint_context = None
_checkpointer: AsyncSqliteSaver | None = None
_graph = None
_thread_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _current_request() -> RoomAgentRequest:
    request = _request_context.get()
    if request is None:
        raise RuntimeError("RoomAI Agent graph requires an active room request")
    return request


def _supervisor(state: RoomAgentState) -> dict:
    request = _current_request()
    return {
        "intent": request.mode,
        "current_prompt": request.prompt[:800],
    }


def _route(state: RoomAgentState) -> str:
    return state.get("intent", "conversation")


async def _answer_node(state: RoomAgentState) -> dict:
    request = _current_request()
    route = state.get("intent", "conversation")
    prior_turns = state.get("turns", [])[-4:]
    structured_context = request.structured_context
    if prior_turns:
        continuity = "LangGraph checkpoint 中最近几轮 Agent 连续上下文（仅作补充，事实以讨论记录为准）：\n"
        continuity += json.dumps(prior_turns, ensure_ascii=False)
        structured_context = f"{structured_context}\n{continuity}" if structured_context else continuity

    result = await answer_room_prompt(
        request.topic,
        request.history,
        request.prompt,
        model=request.model,
        session_id=request.session_id,
        phase=request.phase,
        summary=request.summary,
        structured_context=structured_context,
        member_names=request.member_names,
        external_sources=request.external_sources,
        on_delta=request.on_delta,
    )
    request.output = result
    if not result:
        return {"last_route": route}
    turn: AgentTurn = {
        "prompt": request.prompt[:800],
        "response": result[:1200],
        "route": route,
    }
    return {"turns": [*state.get("turns", []), turn][-4:], "last_route": route}


def _checkpoint_turns(state: RoomAgentState, request: RoomAgentRequest, response: str) -> list[AgentTurn]:
    turn: AgentTurn = {
        "prompt": request.prompt[:800],
        "response": response[:1200],
        "route": state.get("intent", request.mode),
    }
    return [*state.get("turns", []), turn][-4:]


async def _analysis_node(state: RoomAgentState) -> dict:
    request = _current_request()
    result = await analyze_room(
        request.session_id,
        request.model,
        force=request.force_analysis,
        auto_guidance=request.auto_guidance,
    )
    request.output = result
    if not result:
        return {"last_route": "analysis"}
    return {
        "turns": _checkpoint_turns(state, request, result.get("message", "")),
        "last_route": "analysis",
    }


async def _vote_node(state: RoomAgentState) -> dict:
    request = _current_request()
    result = await propose_vote(
        request.topic,
        request.history,
        model=request.model,
        session_id=request.session_id,
        detail=request.prompt,
    )
    request.output = result
    if not result:
        return {"last_route": "vote"}
    response = json.dumps(result, ensure_ascii=False)
    return {"turns": _checkpoint_turns(state, request, response), "last_route": "vote"}


async def _diagram_node(state: RoomAgentState) -> dict:
    request = _current_request()
    result = await generate_mermaid_diagram(
        request.topic,
        request.history,
        request.prompt,
        diagram_type=request.diagram_type,
        model=request.model,
        session_id=request.session_id,
    )
    request.output = result
    if not result:
        return {"last_route": "diagram"}
    response = result.get("explanation") or "已生成方案图。"
    return {"turns": _checkpoint_turns(state, request, response), "last_route": "diagram"}


async def start_room_agent_graph() -> None:
    global _checkpoint_context, _checkpointer, _graph
    if _graph is not None:
        return
    Path(LANGGRAPH_CHECKPOINT_PATH).parent.mkdir(parents=True, exist_ok=True)
    _checkpoint_context = AsyncSqliteSaver.from_conn_string(str(LANGGRAPH_CHECKPOINT_PATH))
    _checkpointer = await _checkpoint_context.__aenter__()
    builder = StateGraph(RoomAgentState)
    builder.add_node("supervisor", _supervisor)
    builder.add_node("conversation", _answer_node)
    builder.add_node("research", _answer_node)
    builder.add_node("analysis", _analysis_node)
    builder.add_node("vote", _vote_node)
    builder.add_node("diagram", _diagram_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        _route,
        {"conversation": "conversation", "research": "research", "analysis": "analysis", "vote": "vote", "diagram": "diagram"},
    )
    builder.add_edge("conversation", END)
    builder.add_edge("research", END)
    builder.add_edge("analysis", END)
    builder.add_edge("vote", END)
    builder.add_edge("diagram", END)
    _graph = builder.compile(checkpointer=_checkpointer)


async def stop_room_agent_graph() -> None:
    global _checkpoint_context, _checkpointer, _graph
    if _checkpoint_context is not None:
        await _checkpoint_context.__aexit__(None, None, None)
    _checkpoint_context = None
    _checkpointer = None
    _graph = None


async def run_room_agent(
    *,
    session_id: str,
    topic: str,
    history: list[dict],
    prompt: str,
    model: str,
    phase: str,
    summary: str | None = None,
    structured_context: str | None = None,
    member_names: list[str] | None = None,
    external_sources: list[dict] | None = None,
    mode: AgentMode = "conversation",
    force_analysis: bool = False,
    auto_guidance: bool = True,
    diagram_type: str = "flowchart",
    on_delta: Callable[[str], Awaitable[None] | None] | None = None,
) -> str | dict[str, Any] | None:
    if _graph is None:
        raise RuntimeError("RoomAI Agent graph has not been started")
    request = RoomAgentRequest(
        session_id=session_id,
        topic=topic,
        history=history,
        prompt=prompt,
        model=model,
        phase=phase,
        summary=summary,
        structured_context=structured_context,
        member_names=member_names or [],
        external_sources=external_sources or [],
        mode=mode,
        force_analysis=force_analysis,
        auto_guidance=auto_guidance,
        diagram_type=diagram_type,
        on_delta=on_delta,
    )
    context_token = _request_context.set(request)
    try:
        async with _thread_locks[session_id]:
            await _graph.ainvoke(
                {"current_prompt": prompt[:800]},
                config={"configurable": {"thread_id": session_id}},
            )
        return request.output
    finally:
        _request_context.reset(context_token)


async def delete_room_agent_checkpoint(session_id: str) -> None:
    if _checkpointer is None:
        return
    async with _thread_locks[session_id]:
        await _checkpointer.aget_tuple({"configurable": {"thread_id": session_id}})
        await _checkpointer.adelete_thread(session_id)

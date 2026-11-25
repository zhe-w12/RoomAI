from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..database import database
from .diagram import DIAGRAM_TYPES, generate_mermaid_diagram


@dataclass
class ToolContext:
    """Room context every tool handler receives."""

    session_id: str
    topic: str
    history: list[dict]
    model: str | None


@dataclass
class ToolResult:
    """What a tool returns to the Agent Loop.

    content: text fed back to the model as the tool result.
    attachment: optional Markdown appended to the final user-facing reply
    (e.g. a rendered diagram). Kept separate so the model does not have to
    copy long generated output verbatim into its answer.
    """

    content: str
    attachment: str | None = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], Awaitable[ToolResult]]


async def _handle_diagram(ctx: ToolContext, args: dict) -> ToolResult:
    subject = str(args.get("subject") or "").strip()
    if not subject:
        return ToolResult(content="参数错误：subject 不能为空，请用一句话说清要画什么。")
    subject = subject[:500]
    diagram_type = str(args.get("diagram_type") or "flowchart").strip()
    if diagram_type not in DIAGRAM_TYPES:
        return ToolResult(
            content=f"参数错误：不支持的图类型 {diagram_type!r}，可用类型：{', '.join(DIAGRAM_TYPES)}。"
        )
    try:
        result = await generate_mermaid_diagram(
            ctx.topic,
            ctx.history,
            subject,
            diagram_type=diagram_type,
            model=ctx.model,
            session_id=ctx.session_id,
        )
    except Exception:
        return ToolResult(content="画图工具执行失败（内部错误）。可以换个说法重试一次。")
    if not result:
        return ToolResult(content="这次画图没成功，可能是模型暂时没响应或生成的代码没通过校验。可以换个说法重试一次。")
    explanation = str(result.get("explanation") or "").strip()
    content = json.dumps(
        {
            "status": "ok",
            "diagram_type": result["diagram_type"],
            "explanation": explanation,
            "note": "图表已生成，会自动附在你的回复后面展示；你只需要用一两句话介绍它，不要粘贴 Mermaid 代码。",
        },
        ensure_ascii=False,
    )
    return ToolResult(content=content, attachment=f"```mermaid\n{result['code']}\n```")


# search_history 返回的单条消息最多展示多少字，避免一次塞太多上下文。
_SEARCH_EXCERPT_CHARS = 300
_SEARCH_MAX_RESULTS = 10


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so a user keyword is matched literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _handle_search_history(ctx: ToolContext, args: dict) -> ToolResult:
    """Search the room's full message history by keyword (memory layer 4).

    Scoped to the current room only; recalled messages are excluded.
    Returns plain text the model can quote directly — no attachment.
    """
    if not ctx.session_id:
        return ToolResult(content="搜索失败：没有房间上下文。")
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(content="参数错误：query 不能为空，请给出一两个关键词，例如“小程序成本”。")
    query = query[:200]
    try:
        limit = int(args.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, _SEARCH_MAX_RESULTS))
    # Split on whitespace; a single Chinese phrase stays one term (substring match).
    terms = [term for term in re.split(r"\s+", query) if term][:5]
    conditions = " OR ".join("content LIKE ? ESCAPE '\\' OR image_summary LIKE ? ESCAPE '\\'" for _ in terms)
    parameters: list[object] = [ctx.session_id]
    for term in terms:
        pattern = f"%{_escape_like(term)}%"
        parameters.extend((pattern, pattern))
    parameters.append(limit)
    with database() as connection:
        rows = connection.execute(
            f"""SELECT sender_name, sender_type, content, image_summary, created_at
                FROM messages
                WHERE session_id = ? AND is_recalled = 0 AND ({conditions})
                ORDER BY id DESC LIMIT ?""",
            parameters,
        ).fetchall()
    if not rows:
        return ToolResult(content=f"在全量历史消息里没有找到包含“{query}”的内容。可以换个关键词再试一次。")
    lines = [f"在历史消息中找到 {len(rows)} 条与“{query}”相关的内容（按时间从近到远）："]
    for row in rows:
        text = str(row["content"] or "") or str(row["image_summary"] or "")
        excerpt = text[:_SEARCH_EXCERPT_CHARS]
        if len(text) > _SEARCH_EXCERPT_CHARS:
            excerpt += "…"
        role = "RoomAI" if row["sender_type"] == "agent" else str(row["sender_name"])
        created = str(row["created_at"] or "")[:16].replace("T", " ")
        lines.append(f"【{role} · {created}】{excerpt}")
    return ToolResult(content="\n".join(lines))


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "generate_mermaid_diagram": ToolDefinition(
        name="generate_mermaid_diagram",
        description=(
            "把讨论里已经确定的方案、流程或思路画成 Mermaid 图。"
            "当用户想看图、想把方案可视化时调用。只依据讨论内容画图，不编造。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "画什么：用一句话说清要画的内容，例如“先做小程序 MVP 的流程”",
                },
                "diagram_type": {
                    "type": "string",
                    "enum": list(DIAGRAM_TYPES),
                    "description": (
                        "图类型：flowchart 流程图（默认）、mindmap 思维导图、"
                        "sequenceDiagram 时序图、classDiagram 类图、stateDiagram-v2 状态图"
                    ),
                },
            },
            "required": ["subject"],
        },
        handler=_handle_diagram,
    ),
    "search_history": ToolDefinition(
        name="search_history",
        description=(
            "按关键词搜索当前房间的全量历史消息（记忆的第 4 层）。"
            "当用户问起之前讨论过的内容，而你手头的最近消息和滚动总结里没有时调用，"
            "例如“之前说的成本是多少”“最早是谁提的这个想法”“上次投票结果是什么”。"
            "只搜索当前房间，不跨房间；已撤回的消息不会出现。返回匹配消息的发送人、时间和内容节选，"
            "最多 10 条。最近消息里已经有的内容不要调用这个工具。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，一两个词即可，例如“小程序成本”“投票结果”。不要写完整句子。",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回几条，默认 5，上限 10。",
                },
            },
            "required": ["query"],
        },
        handler=_handle_search_history,
    ),
}


def tool_definitions() -> list[dict]:
    """OpenAI function-calling schemas for every registered tool."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in TOOL_REGISTRY.values()
    ]


async def execute_tool(name: str, args: dict, ctx: ToolContext) -> ToolResult:
    """Run one registered tool call. Never raises: failures become text
    results the model can react to (retry with fixed args, or tell the user)."""
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        available = ", ".join(TOOL_REGISTRY) or "（暂无可用工具）"
        return ToolResult(content=f"未知工具：{name}。可用工具：{available}。")
    if not isinstance(args, dict):
        return ToolResult(content=f"工具 {name} 的参数必须是 JSON 对象。")
    try:
        return await tool.handler(ctx, args)
    except Exception:
        return ToolResult(content=f"工具 {name} 执行失败（内部错误）。")

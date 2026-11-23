from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

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

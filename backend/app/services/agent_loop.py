from __future__ import annotations

from .agent import build_room_messages, complete_chat
from .tools import ToolContext, execute_tool, tool_definitions

# 工具轮数上限：防止模型反复调工具停不下来。
MAX_LOOP_ROUNDS = 5

LOOP_SYSTEM_NOTE = (
    "你可以使用工具来帮助回答。调用工具后，你会收到工具返回的结果，再决定是继续调用工具还是直接回复用户；"
    "不需要工具时直接用文字回复，不要为了用工具而用工具。"
    "画图工具返回后，图表会自动附在你的回复后面展示，你只需要用一两句话介绍它，不要自己粘贴 Mermaid 代码。"
)


def _finish(content: str, attachments: list[str]) -> str | None:
    parts = []
    if content:
        parts.append(content)
    elif attachments:
        parts.append("这是根据讨论画的图：")
    for attachment in attachments:
        if attachment not in parts and attachment not in content:
            parts.append(attachment)
    answer = "\n\n".join(parts).strip()
    return answer or None


async def run_agent_loop(
    topic: str,
    history: list[dict],
    prompt: str,
    *,
    model: str | None = None,
    session_id: str | None = None,
    phase: str = "diverge",
    summary: str | None = None,
    structured_context: str | None = None,
    member_names: list[str] | None = None,
    external_sources: list[dict] | None = None,
) -> str | None:
    """Main Agent Loop: the model decides each step — call a tool or answer.

    Each round the model either answers directly (loop ends) or calls one or
    more registered tools; tool results go back into the conversation and the
    model decides again. Runs at most MAX_LOOP_ROUNDS tool rounds, then makes
    one last call without tools to force a plain-text reply.

    Returns the final reply text (tool attachments such as diagrams are
    appended), or None when the model cannot be reached — the caller shows
    its fallback message in that case.
    """
    ctx = ToolContext(
        session_id=session_id or "",
        topic=topic,
        history=history,
        model=model,
    )
    messages = build_room_messages(
        topic,
        history,
        prompt,
        phase=phase,
        summary=summary,
        structured_context=structured_context,
        member_names=member_names,
        external_sources=external_sources,
        extra_system=LOOP_SYSTEM_NOTE,
    )
    tools = tool_definitions()
    attachments: list[str] = []
    for _ in range(MAX_LOOP_ROUNDS):
        result = await complete_chat(messages, model=model, session_id=session_id, tools=tools)
        if not result:
            return None
        tool_calls = result.get("tool_calls") or []
        content = (result.get("content") or "").strip()
        if not tool_calls:
            return _finish(content, attachments)
        # Only calls with an id and a name can be answered with a tool message;
        # malformed calls are dropped and the model's text (if any) stands.
        valid_calls = [call for call in tool_calls if call.get("id") and call.get("name")]
        if not valid_calls:
            return _finish(content, attachments)
        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [call["raw"] for call in valid_calls],
            }
        )
        for call in valid_calls:
            if call.get("arguments_error"):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": "工具参数解析失败：arguments 不是合法的 JSON 对象，请用合法 JSON 重新调用。",
                    }
                )
                continue
            executed = await execute_tool(call["name"], call["arguments"], ctx)
            if executed.attachment:
                attachments.append(executed.attachment)
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": executed.content}
            )
    # Rounds exhausted: one last call without tools, forcing a plain reply.
    messages.append({"role": "user", "content": "请直接用文字回复用户，不要再调用工具。"})
    result = await complete_chat(messages, model=model, session_id=session_id)
    if not result:
        return None
    return _finish((result.get("content") or "").strip(), attachments)

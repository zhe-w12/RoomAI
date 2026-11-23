from __future__ import annotations

import json
import re

from .agent import complete_chat, history_message

MAX_DIAGRAM_CHARS = 6000

# 图类型 -> 该类型合法的 Mermaid 首行关键字（flowchart 接受 graph 同义词）。
DIAGRAM_TYPES = {
    "flowchart": ("flowchart", "graph"),
    "mindmap": ("mindmap",),
    "sequenceDiagram": ("sequenceDiagram",),
    "classDiagram": ("classDiagram",),
    "stateDiagram-v2": ("stateDiagram-v2",),
}

_DIAGRAM_TYPE_LABELS = {
    "flowchart": "流程图（flowchart）",
    "mindmap": "思维导图（mindmap）",
    "sequenceDiagram": "时序图（sequenceDiagram）",
    "classDiagram": "类图（classDiagram）",
    "stateDiagram-v2": "状态图（stateDiagram-v2）",
}

# 任意一种支持的图类型：沿用纪要里原有的校验思路。
_DIAGRAM_KEYWORD_RE = re.compile(
    r"(?:flowchart|graph|mindmap|sequenceDiagram|classDiagram|stateDiagram-v2)[^\n]*[\s\S]*"
)

_FENCE_RE = re.compile(r"```(?:mermaid)?\s*\n?(.*?)```", re.DOTALL)


def sanitize_mermaid_code(code: str, diagram_type: str | None = None) -> str:
    """Strip code fences, validate the diagram keyword, and cap the length.

    Returns "" when the text is not a plausible Mermaid diagram. When a
    diagram_type is given the code must start with that type; otherwise any
    supported type is accepted (the memo validation behaviour).
    """
    text = (code or "").strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    text = text[:MAX_DIAGRAM_CHARS].strip()
    if not text:
        return ""
    if diagram_type:
        keywords = DIAGRAM_TYPES.get(diagram_type, ())
        if not keywords:
            return ""
        pattern = re.compile(r"(?:" + "|".join(keywords) + r")[^\n]*[\s\S]*")
        return text if re.fullmatch(pattern, text) else ""
    return text if re.fullmatch(_DIAGRAM_KEYWORD_RE, text) else ""


async def generate_mermaid_diagram(
    topic: str,
    history: list[dict],
    subject: str,
    *,
    diagram_type: str = "flowchart",
    model: str | None = None,
    session_id: str | None = None,
) -> dict | None:
    """Diagram tool: draw a Mermaid diagram of the agreed plan.

    Asks the model for structured JSON (mermaid code + a short explanation),
    validates the code with sanitize_mermaid_code, and retries once with a
    correction hint when validation fails.

    Returns {"code": ..., "diagram_type": ..., "explanation": ...}, or None
    when the model fails or the code does not validate.
    """
    diagram_type = diagram_type if diagram_type in DIAGRAM_TYPES else "flowchart"
    label = _DIAGRAM_TYPE_LABELS[diagram_type]
    system = (
        "你是 RoomAI 画图工具。只依据房间讨论画图，不编造讨论里没有的步骤或结论。"
        f"只输出 JSON，包含两个字段：\"mermaid\" 是完整的 Mermaid {label} 代码，"
        "必须以该图类型关键字开头，不要加 ``` 包裹；\"explanation\" 是一两句话的中文说明。"
        "节点文字要简短，避免括号、引号等 Mermaid 容易解析失败的字符。"
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(history_message(item) for item in history[-30:] if not item.get("is_recalled"))
    messages.append({
        "role": "user",
        "content": f"房间主题：{topic}\n画什么：{subject}\n图类型：{label}",
    })
    for attempt in range(2):
        result = await complete_chat(
            messages, model=model, session_id=session_id, json_mode=True, temperature=0.2
        )
        if not result:
            return None
        try:
            parsed = json.loads(result["content"])
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        code = sanitize_mermaid_code(
            str(parsed.get("mermaid", "")), diagram_type
        ) if isinstance(parsed, dict) else ""
        if code:
            explanation = str(parsed.get("explanation", "")).strip()[:400]
            return {"code": code, "diagram_type": diagram_type, "explanation": explanation}
        if attempt == 0:
            messages.append({
                "role": "user",
                "content": f"上一次输出的 Mermaid 代码无法通过校验。请只输出合法的 Mermaid {label} 代码，"
                "必须以该图类型关键字开头，不要加 ``` 包裹。",
            })
    return None

from __future__ import annotations

import httpx

from ..config import MODEL_API_KEY, MODEL_BASE_URL, MODEL_NAME, MODEL_TIMEOUT_SECONDS


COMMAND_PROMPTS = {
    "/总结": "请根据当前讨论，分成共识、分歧、待办三部分做简洁总结。不要补充讨论里没有的信息。",
    "/评估": "评估最近提出的核心想法：技术可行性、人力成本、时间周期、风险、创新性、团队匹配度，每项 1 到 5 分并简述理由。",
    "/投票": "整理讨论中最需要团队决定的分歧，给出清楚的问题和 2 到 4 个中立选项。",
    "/画图": "根据讨论里已经确定的方案，用 Mermaid flowchart 代码画出主要步骤，并简短解释。",
    "/挑战": "站在红队角度，提出当前推荐方向最关键的 1 到 2 个风险，用问题表达，不要长篇否定。",
    "/待办": "从讨论中找出明确的下一步待办，并列出负责人（如果讨论中提到）和待确认事项。",
    "/结束": "先检查用户、技术、时间、成本、风险五个维度是否讨论充分。指出尚未覆盖的内容，等待房主确认后再结束。",
}


async def answer_room_prompt(topic: str, history: list[dict], prompt: str) -> str | None:
    if not MODEL_API_KEY:
        return None
    messages = [
        {
            "role": "system",
            "content": (
                "你是 RoomAI，一个帮助团队讨论立项想法的中文协作助手。紧扣房间主题和已有对话，"
                "回答具体、克制、易读；不要伪造成员观点、决定、评分或待办。若依据不足，明确说还需要讨论什么。"
                f"\n房间主题：{topic}"
            ),
        }
    ]
    for item in history[-30:]:
        if item.get("is_recalled"):
            continue
        role = "assistant" if item.get("sender_type") == "agent" else "user"
        content = f"{item.get('sender_name', '成员')}: {item.get('content') or ''}"
        if item.get("image_path"):
            content += "（消息附有图片）"
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt})
    try:
        async with httpx.AsyncClient(timeout=MODEL_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{MODEL_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {MODEL_API_KEY}"},
                json={"model": MODEL_NAME, "temperature": 0.55, "messages": messages},
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return content.strip() if isinstance(content, str) and content.strip() else None
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return None

from __future__ import annotations

import json

from .agent import complete_chat


async def propose_vote(topic: str, history: list[dict], *, model: str, session_id: str, detail: str = "") -> dict | None:
    messages = [
        {
            "role": "system",
            "content": (
                "你是 RoomAI 的协作讨论助手。根据成员已经表达的真实分歧，生成一个中立投票问题和 2 到 4 个清晰、互斥的选项。"
                "不要凭空制造争议；没有足够分歧时，将 question 说明为需要团队决定的关键问题。只返回 JSON："
                '{"question":"...","options":["...","..."]}。'
                f"\n讨论主题：{topic}"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"messages": history[-30:], "request": detail}, ensure_ascii=False),
        },
    ]
    result = await complete_chat(messages, model=model, session_id=session_id, json_mode=True, temperature=0.25)
    if not result:
        return None
    try:
        data = json.loads(result["content"])
        question = str(data.get("question", "")).strip()[:300]
        options = []
        for option in data.get("options", []):
            label = str(option).strip()[:180]
            if label and label not in options:
                options.append(label)
        if len(question) < 4 or len(options) < 2:
            return None
        return {"question": question, "options": options[:4]}
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None

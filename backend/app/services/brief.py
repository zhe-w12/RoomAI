from __future__ import annotations

import json

from ..config import MODEL_API_KEY, MODEL_NAME
from .agent import complete_chat


def fallback_brief(topic: str) -> dict:
    return {
        "opening": f"欢迎来到「{topic}」讨论室。先把目标用户、要解决的问题和成功标准说清楚，再一起比较可行的方案。",
        "prep_checklist": [
            "我们最想解决的具体问题是什么？",
            "谁会使用这个方案，他们现在怎么解决？",
            "怎样判断这次讨论得出了有效结论？",
        ],
    }


async def create_room_brief(topic: str, session_id: str | None = None, model: str | None = None) -> dict:
    if not MODEL_API_KEY:
        return fallback_brief(topic)

    result = await complete_chat(
        [
            {
                "role": "system",
                "content": (
                    "你是 RoomAI 立项讨论引导员。根据主题生成一个温和、具体的开场，以及 3 到 5 个简短准备问题。"
                    '只返回 JSON：{"opening":"...","prep_checklist":["..."]}。'
                ),
            },
            {"role": "user", "content": f"讨论主题：{topic}"},
        ],
        model=model or MODEL_NAME,
        session_id=session_id,
        json_mode=True,
        temperature=0.5,
    )
    if not result:
        return fallback_brief(topic)
    try:
        parsed = json.loads(result["content"])
        opening = str(parsed.get("opening", "")).strip()
        questions = parsed.get("prep_checklist", [])
        questions = [str(item).strip() for item in questions if str(item).strip()][:5]
        if not opening or len(questions) < 3:
            return fallback_brief(topic)
        return {"opening": opening, "prep_checklist": questions}
    except (KeyError, IndexError, TypeError, ValueError):
        return fallback_brief(topic)

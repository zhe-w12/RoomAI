from __future__ import annotations

import json

from ..config import MODEL_API_KEY, MODEL_NAME
from .agent import complete_chat


ROOM_TEMPLATES = {
    "general": {
        "id": "general",
        "label": "自由讨论",
        "description": "围绕主题自由发散，再整理共识与下一步。",
        "direction": "先澄清问题和目标，再探索不同方向并逐步收敛。",
        "prep_checklist": [
            "我们最想解决的具体问题是什么？",
            "谁会使用这个方案，他们现在怎么解决？",
            "怎样判断这次讨论得出了有效结论？",
        ],
    },
    "technical-selection": {
        "id": "technical-selection",
        "label": "技术选型",
        "description": "对齐约束，比较技术方案的成本、适配度和风险。",
        "direction": "优先讨论使用场景、现有技术约束、团队经验、维护成本和迁移风险。",
        "prep_checklist": [
            "这个技术要解决什么具体需求，规模和性能要求是什么？",
            "团队现有技术栈与可投入的维护人力有哪些限制？",
            "哪些标准最重要：交付速度、成本、可靠性还是扩展性？",
            "上线前需要验证哪些兼容性或安全风险？",
        ],
    },
    "product-proposal": {
        "id": "product-proposal",
        "label": "产品立项",
        "description": "明确目标用户、问题、验证路径与首期范围。",
        "direction": "优先明确目标用户与痛点，再讨论价值假设、验证方式、首期范围和成功指标。",
        "prep_checklist": [
            "目标用户是谁，他们遇到的具体问题是什么？",
            "目前有什么证据说明这个问题值得解决？",
            "最小可验证方案包含什么，哪些内容先不做？",
            "用什么指标或用户反馈判断立项假设成立？",
        ],
    },
    "event-planning": {
        "id": "event-planning",
        "label": "活动策划",
        "description": "梳理参与人、目标、流程、资源与现场风险。",
        "direction": "优先对齐活动目标与参与人，再梳理流程、场地物料、预算、分工和应急预案。",
        "prep_checklist": [
            "活动希望达成什么结果，面向哪些参与人？",
            "时间、场地、人数和预算有哪些确定限制？",
            "活动流程和关键负责人分别是什么？",
            "现场可能出现哪些风险，如何准备备选方案？",
        ],
    },
}


def fallback_brief(topic: str, template_id: str = "general") -> dict:
    template = ROOM_TEMPLATES.get(template_id, ROOM_TEMPLATES["general"])
    return {
        "opening": f"欢迎来到「{topic}」讨论室。本次采用「{template['label']}」模板，{template['direction']}",
        "prep_checklist": list(template["prep_checklist"]),
    }


async def create_room_brief(topic: str, session_id: str | None = None, model: str | None = None, template_id: str = "general") -> dict:
    template = ROOM_TEMPLATES.get(template_id, ROOM_TEMPLATES["general"])
    if not MODEL_API_KEY:
        return fallback_brief(topic, template_id)

    result = await complete_chat(
        [
            {
                "role": "system",
                "content": (
                    "你是 RoomAI 立项讨论引导员。根据讨论主题和指定模板生成一个温和、具体的开场，以及 3 到 5 个简短准备问题。"
                    '只返回 JSON：{"opening":"...","prep_checklist":["..."]}。'
                ),
            },
            {"role": "user", "content": f"讨论主题：{topic}\n讨论模板：{template['label']}\n模板方向：{template['direction']}"},
        ],
        model=model or MODEL_NAME,
        session_id=session_id,
        json_mode=True,
        temperature=0.5,
    )
    if not result:
        return fallback_brief(topic, template_id)
    try:
        parsed = json.loads(result["content"])
        opening = str(parsed.get("opening", "")).strip()
        questions = parsed.get("prep_checklist", [])
        questions = [str(item).strip() for item in questions if str(item).strip()][:5]
        if not opening or len(questions) < 3:
            return fallback_brief(topic, template_id)
        return {"opening": opening, "prep_checklist": questions}
    except (KeyError, IndexError, TypeError, ValueError):
        return fallback_brief(topic, template_id)

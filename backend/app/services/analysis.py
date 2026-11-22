from __future__ import annotations

import difflib
import json
import math
import re
from datetime import datetime, timezone

from ..database import database
from ..config import AUTO_GUIDANCE_COOLDOWN_SECONDS, EVALUATION_DIMENSIONS
from .agent import complete_chat, history_message


DIMENSIONS = tuple(item["key"] for item in EVALUATION_DIMENSIONS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_list(value, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:600] for item in value if str(item).strip()][:limit]


def _normalized(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def _same_idea(first: str, second: str) -> bool:
    a, b = _normalized(first), _normalized(second)
    if not a or not b:
        return False
    if a in b or b in a:
        return min(len(a), len(b)) >= 7
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.84


def _format_summary(summary: dict, version: int, ideas: list[dict]) -> str:
    consensus = _clean_list(summary.get("consensus"))
    disagreements = _clean_list(summary.get("disagreements"))
    todos = summary.get("todos") if isinstance(summary.get("todos"), list) else []
    lines = [f"### 本轮讨论总结 · v{version}", "", "**共识**"]
    lines.extend(f"- {item}" for item in consensus or ["暂时还没有形成明确共识。"])
    lines.extend(["", "**分歧**"])
    lines.extend(f"- {item}" for item in disagreements or ["暂未发现明确分歧。"])
    lines.extend(["", "**待办**"])
    for item in todos[:8]:
        if isinstance(item, dict) and item.get("title"):
            assignee = str(item.get("assignee", "")).strip()
            lines.append(f"- {item['title']}{f'（负责人：{assignee}）' if assignee else ''}")
    if not any(isinstance(item, dict) and item.get("title") for item in todos):
        lines.append("- 暂无明确待办。")
    decision_points = _clean_list(summary.get("decision_points"), 5)
    if decision_points:
        lines.extend(["", "**待团队拍板**"])
        lines.extend(f"- {item}" for item in decision_points)
    if ideas:
        lines.extend(["", "**本轮新想法**"])
        for idea in ideas[:5]:
            score = idea.get("average_score")
            label = f" · 评估 {score:.1f}/5" if isinstance(score, (int, float)) else ""
            lines.append(f"- **{idea['title']}**{label}：{idea['description']}")
    guide_question = str(summary.get("guide_question", "")).strip()
    if guide_question:
        lines.extend(["", "### 下一步引导", guide_question])
    if summary.get("phase_suggestion") == "converge":
        lines.extend(["", "### 阶段建议", "当前已有可比较的方向，RoomAI 建议房主考虑切换到收敛阶段。"])
    return "\n".join(lines)


async def analyze_room(
    session_id: str,
    model: str,
    *,
    force: bool = False,
    auto_guidance: bool = True,
) -> dict | None:
    with database() as connection:
        room = connection.execute("SELECT * FROM rooms WHERE session_id = ?", (session_id,)).fetchone()
        if not room:
            return None
        latest_summary = connection.execute(
            "SELECT * FROM summaries WHERE session_id = ? ORDER BY version DESC LIMIT 1", (session_id,)
        ).fetchone()
        through_id = int(latest_summary["through_message_id"]) if latest_summary else 0
        rows = connection.execute(
            """SELECT m.*, r.sender_name AS reply_sender_name, r.content AS reply_content,
                      r.is_recalled AS reply_is_recalled
               FROM messages m LEFT JOIN messages r ON r.id = m.reply_to_message_id
               WHERE m.session_id = ? AND m.id > ? ORDER BY m.id DESC LIMIT 30""",
            (session_id, through_id),
        ).fetchall()
        recent = [dict(row) for row in reversed(rows) if not row["is_recalled"]]
        latest_message = connection.execute(
            "SELECT COALESCE(MAX(id), 0) AS id FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()["id"]
        existing_ideas = connection.execute(
            """SELECT i.id, i.title, i.description, i.strengths, i.tradeoffs, i.proposer_name,
                      (SELECT e.dimensions FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS dimensions
               FROM ideas i WHERE i.session_id = ? ORDER BY i.updated_at DESC LIMIT 20""",
            (session_id,),
        ).fetchall()
        existing_todos = connection.execute(
            "SELECT id, title, assignee, status, due_at FROM todos WHERE session_id = ? ORDER BY id DESC LIMIT 30",
            (session_id,),
        ).fetchall()
        existing_decisions = connection.execute(
            "SELECT title, outcome, created_at FROM decisions WHERE session_id = ? ORDER BY id DESC LIMIT 20",
            (session_id,),
        ).fetchall()
        existing_image_summaries = connection.execute(
            """SELECT id AS message_id, sender_name, image_summary, created_at FROM messages
               WHERE session_id = ? AND image_summary <> '' ORDER BY id DESC LIMIT 30""",
            (session_id,),
        ).fetchall()
        members = [row["nickname"] for row in connection.execute(
            "SELECT nickname FROM visitors WHERE session_id = ?", (session_id,)
        ).fetchall()]
    if not recent and not force:
        return None
    previous = json.loads(latest_summary["content"]) if latest_summary else {}
    dimension_schema = json.dumps({key: 3 for key in DIMENSIONS}, ensure_ascii=False)
    dimension_guidance = "；".join(
        f"{item['key']}（{item['label']}）：{item['guidance']}" for item in EVALUATION_DIMENSIONS
    )
    prompt = {
        "previous_summary": previous,
        "phase": room["phase"],
        "known_ideas": [dict(item) for item in existing_ideas],
        "known_todos": [dict(item) for item in existing_todos],
        "known_decisions": [dict(item) for item in existing_decisions],
        "known_image_summaries": [dict(item) for item in existing_image_summaries],
        "member_names": members,
        "messages": [
            {
                "sender": item["sender_name"],
                "content": item["content"],
                "id": item["id"],
                "has_image": bool(item.get("image_path") and not item.get("is_recalled")),
            }
            for item in recent
        ],
    }
    system = (
        "你是 RoomAI 的讨论分析员。只整理消息中真实出现的观点，不要补充事实。"
        "从最近消息增量更新滚动总结，识别值得进入想法池的新想法，并按 1-5 分评估配置的维度。"
        f"评估维度：{dimension_guidance}。"
        "disagreements 只记录成员明确表达相反立场的具体事项；单纯未决定、缺少信息或同一立场的补充不算分歧。"
        "房间处于发散阶段时，鼓励补充方向，不急于选定方案；处于收敛阶段时，优先指出待决定事项、风险和下一步。"
        "decision_points 只记录讨论中已经明确暴露、需要团队做出选择或确认的关键决策；不要把一般待办、风险或信息缺口当成决策。"
        "只在收敛阶段输出 decision_points，最多 5 条；发散阶段必须返回空数组。"
        "如果讨论明显跑题、停滞或关键立项信息缺失，在 guide_question 中给出一个简短、开放的追问；否则留空。"
        "仅依据最近消息判断群体讨论状态，不推断或诊断任何个人情绪；state 必须从 active、steady、quiet、tense 中选一个；"
        "tense 只用于明确的冲突或对立表达，quiet 只用于可观察的低参与，证据不足时用 steady。"
        "atmosphere.evidence 只描述消息中可观察到的互动信号，不写成员姓名；adjustment 说明 RoomAI 下一轮如何调整节奏或提问方式。"
        "当房间处于发散阶段，且已有多个清晰候选方向、讨论开始进入比较取舍时，phase_suggestion 可设为 converge；否则为 null。"
        "known_ideas 中若有重复想法，请在 merge_into 填对应 id；新想法的 proposer_name 只能使用 member_names 中的昵称；"
        "如果来源消息的 sender 是‘匿名成员’，proposer_name 必须填写‘匿名成员’，绝不能猜测身份；"
        "结合 known_decisions 区分已经记录的决策和仍待团队拍板的 decision_points；只有新消息明确推翻或更新时才视为变化。"
        "source_message_id 必须来自输入消息 id。待办仅记录明确提到的任务，待办 source_message_id 也必须来自输入消息 id。"
        "对 recent messages 中 has_image=true 的消息，为每张图片写一句客观、简短的 image_summaries 描述；无法辨认时留空。"
        "image_summaries.message_id 必须来自这些带图片的消息 id，不要对同一张图片猜测图片外的信息。"
        "待办应尽量关联 known_todos 中的 todo_id；只有消息里明确提到时才给负责人和截止日期，状态可为 open、done 或 dropped。"
        "只返回 JSON，格式为："
        '{"summary":{"consensus":["..."],"disagreements":["..."],"decision_points":["..."],"todos":[{"todo_id":1,"title":"...","assignee":"","status":"open","due_at":"YYYY-MM-DD","source_message_id":1}],'
        '"atmosphere":{"state":"active|steady|quiet|tense","confidence":0.0,"evidence":"...","adjustment":"..."},'
        '"dimension_coverage":{"user":false,"technology":false,"time":false,"cost":false,"risk":false},"guide_question":"","phase_suggestion":null},'
        '"image_summaries":[{"message_id":1,"description":"..."}],'
        '"ideas":[{"title":"...","description":"...","proposer_name":"","source_message_id":1,"merge_into":null,'
        f'"dimensions":{dimension_schema},"rationale":"...","strengths":"...","tradeoffs":"..."}}]}}。'
        "ideas 最多 5 个；没有就返回空数组。每个评分都应有简短理由，证据不足时 dimensions 返回空对象。"
        "strengths 和 tradeoffs 分别概括讨论中有依据的优势与代价/风险；没有明确证据时返回空字符串。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]
    # Include recent uploaded images as multimodal inputs when available.
    multimodal = [history_message(item) for item in recent]
    if any(isinstance(item.get("content"), list) for item in multimodal):
        image_parts = []
        for source_message, formatted in zip(recent, multimodal):
            if isinstance(formatted.get("content"), list):
                image_parts.extend([
                    {"type": "text", "text": f"这张图片来自消息 #{source_message['id']}（{source_message['sender_name']}）。"},
                    *[
                        part
                        for part in formatted["content"]
                        if part.get("type") == "image_url"
                    ],
                ])
        messages[1]["content"] = [
            {"type": "text", "text": json.dumps(prompt, ensure_ascii=False)},
            *image_parts,
        ]
    result = None
    payload = None
    for attempt in range(2):
        result = await complete_chat(messages, model=model, session_id=session_id, json_mode=True, temperature=0.2)
        if not result:
            return None
        try:
            payload = json.loads(result["content"])
            break
        except (json.JSONDecodeError, TypeError):
            if attempt == 1:
                return None
            messages = [*messages, {"role": "user", "content": "上一条输出不是有效 JSON。请按指定 JSON 格式重新输出，不要使用 Markdown 代码围栏。"}]
    summary_data = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary_data, dict):
        return None
    valid_recent_ids = {int(item["id"]) for item in recent}
    valid_image_ids = {
        int(item["id"])
        for item in recent
        if item.get("image_path") and not item.get("is_recalled")
    }
    image_summaries: dict[int, str] = {}
    raw_image_summaries = payload.get("image_summaries") if isinstance(payload, dict) else []
    if isinstance(raw_image_summaries, list):
        for item in raw_image_summaries:
            if not isinstance(item, dict):
                continue
            message_id = item.get("message_id")
            description = str(item.get("description", "")).strip()[:400]
            if isinstance(message_id, int) and message_id in valid_image_ids and description:
                image_summaries[message_id] = description
    valid_todo_ids = {int(item["id"]) for item in existing_todos}
    todos = []
    for item in summary_data.get("todos", []):
        if isinstance(item, dict) and str(item.get("title", "")).strip():
            source_id = item.get("source_message_id")
            if not isinstance(source_id, int) or source_id not in valid_recent_ids:
                source_id = None
            todo_id = item.get("todo_id")
            if not isinstance(todo_id, int) or todo_id not in valid_todo_ids:
                todo_id = None
            status = item.get("status") if item.get("status") in {"open", "done", "dropped"} else "open"
            due_at = str(item.get("due_at", "")).strip()[:10]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_at):
                due_at = None
            todos.append({
                "todo_id": todo_id,
                "title": str(item["title"]).strip()[:300],
                "assignee": str(item.get("assignee", "")).strip()[:80],
                "status": status,
                "due_at": due_at,
                "source_message_id": source_id,
            })
    raw_coverage = summary_data.get("dimension_coverage") if isinstance(summary_data.get("dimension_coverage"), dict) else {}
    raw_atmosphere = summary_data.get("atmosphere") if isinstance(summary_data.get("atmosphere"), dict) else {}
    raw_atmosphere_state = raw_atmosphere.get("state")
    atmosphere_state = raw_atmosphere_state if isinstance(raw_atmosphere_state, str) and raw_atmosphere_state in {"active", "steady", "quiet", "tense"} else "steady"
    try:
        atmosphere_confidence = min(1.0, max(0.0, float(raw_atmosphere.get("confidence", 0))))
    except (TypeError, ValueError):
        atmosphere_confidence = 0.0
    if not math.isfinite(atmosphere_confidence):
        atmosphere_confidence = 0.0
    guide_question = str(summary_data.get("guide_question", "")).strip()[:500]
    decision_points = _clean_list(summary_data.get("decision_points"), 5) if room["phase"] == "converge" else []
    phase_suggestion = summary_data.get("phase_suggestion")
    if phase_suggestion != "converge" or room["phase"] != "diverge":
        phase_suggestion = None
    summary_data = {
        "consensus": _clean_list(summary_data.get("consensus")),
        "disagreements": _clean_list(summary_data.get("disagreements")),
        "todos": todos[:8],
        "dimension_coverage": {
            key: bool(raw_coverage.get(key, False))
            for key in ("user", "technology", "time", "cost", "risk")
        },
        "guide_question": guide_question,
        "decision_points": decision_points,
        "phase_suggestion": phase_suggestion,
        "atmosphere": {
            "state": atmosphere_state,
            "confidence": atmosphere_confidence,
            "evidence": str(raw_atmosphere.get("evidence", "")).strip()[:400],
            "adjustment": str(raw_atmosphere.get("adjustment", "")).strip()[:300],
        },
    }
    created_at = _now()
    saved_ideas: list[dict] = []
    with database() as connection:
        if auto_guidance and guide_question:
            if AUTO_GUIDANCE_COOLDOWN_SECONDS:
                cooldown_cutoff = (
                    datetime.now(timezone.utc).timestamp() - AUTO_GUIDANCE_COOLDOWN_SECONDS
                )
                cutoff_at = datetime.fromtimestamp(cooldown_cutoff, timezone.utc).isoformat()
                cursor = connection.execute(
                    """UPDATE rooms SET last_guidance_at = ?
                       WHERE session_id = ? AND (last_guidance_at IS NULL OR last_guidance_at <= ?)""",
                    (created_at, session_id, cutoff_at),
                )
            else:
                cursor = connection.execute(
                    "UPDATE rooms SET last_guidance_at = ? WHERE session_id = ?",
                    (created_at, session_id),
                )
            if cursor.rowcount != 1:
                guide_question = ""
                summary_data["guide_question"] = ""
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM summaries WHERE session_id = ?", (session_id,)
        ).fetchone()["version"]
        connection.execute(
            "INSERT INTO summaries (session_id, version, content, through_message_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, version, json.dumps(summary_data, ensure_ascii=False), latest_message, created_at),
        )
        for message_id, description in image_summaries.items():
            connection.execute(
                "UPDATE messages SET image_summary = ? WHERE session_id = ? AND id = ?",
                (description, session_id, message_id),
            )
        valid_source_ids = valid_recent_ids
        known_member_names = set(members)
        for raw in (payload.get("ideas") or [])[:5]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title", "")).strip()[:160]
            description = str(raw.get("description", "")).strip()[:900]
            strengths = str(raw.get("strengths", "")).strip()[:500]
            tradeoffs = str(raw.get("tradeoffs", "")).strip()[:500]
            if len(title) < 3 or not description:
                continue
            merge_id = raw.get("merge_into")
            found = None
            if isinstance(merge_id, int):
                found = connection.execute(
                    "SELECT id FROM ideas WHERE id = ? AND session_id = ?", (merge_id, session_id)
                ).fetchone()
            if not found:
                for known in connection.execute("SELECT id, title FROM ideas WHERE session_id = ?", (session_id,)).fetchall():
                    if _same_idea(title, known["title"]):
                        found = known
                        break
            if found:
                existing_details = connection.execute(
                    "SELECT strengths, tradeoffs FROM ideas WHERE id = ?", (found["id"],)
                ).fetchone()
                if existing_details:
                    strengths = strengths or existing_details["strengths"]
                    tradeoffs = tradeoffs or existing_details["tradeoffs"]
            source_id = raw.get("source_message_id")
            if not isinstance(source_id, int) or source_id not in valid_source_ids:
                source_id = None
            source_message = next((item for item in recent if item["id"] == source_id), None)
            proposer = str(raw.get("proposer_name", "")).strip()
            source_sender = source_message["sender_name"] if source_message else ""
            if source_sender == "匿名成员":
                proposer = "匿名成员"
            elif proposer not in known_member_names:
                proposer = ""
            if found and source_message and source_message.get("visitor_id"):
                connection.execute(
                    "INSERT OR IGNORE INTO idea_votes (idea_id, visitor_id, stance, created_at) VALUES (?, ?, 'supplement', ?)",
                    (found["id"], source_message["visitor_id"], created_at),
                )
            if found:
                idea_id = found["id"]
                connection.execute(
                    "UPDATE ideas SET description = ?, strengths = ?, tradeoffs = ?, updated_at = ? WHERE id = ?",
                    (description, strengths, tradeoffs, created_at, idea_id),
                )
            else:
                cursor = connection.execute(
                    "INSERT INTO ideas (session_id, title, description, strengths, tradeoffs, proposer_name, source_message_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, title, description, strengths, tradeoffs, proposer, source_id, created_at, created_at),
                )
                idea_id = cursor.lastrowid
            dimensions = raw.get("dimensions") if isinstance(raw.get("dimensions"), dict) else {}
            valid_dimensions = {
                key: min(5, max(1, int(dimensions[key])))
                for key in DIMENSIONS
                if isinstance(dimensions.get(key), (int, float)) and 1 <= int(dimensions[key]) <= 5
            }
            rationale = str(raw.get("rationale", "")).strip()[:500]
            if valid_dimensions:
                connection.execute(
                    "INSERT INTO evaluations (idea_id, dimensions, rationale, model, created_at) VALUES (?, ?, ?, ?, ?)",
                    (idea_id, json.dumps(valid_dimensions), rationale, model, created_at),
                )
            idea = connection.execute(
                """SELECT i.*, (SELECT dimensions FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS dimensions,
                          (SELECT rationale FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS rationale
                   FROM ideas i WHERE i.id = ?""", (idea_id,)
            ).fetchone()
            item = dict(idea)
            item["dimensions"] = json.loads(item["dimensions"] or "{}")
            item["average_score"] = round(sum(item["dimensions"].values()) / len(item["dimensions"]), 1) if item["dimensions"] else None
            saved_ideas.append(item)
        touched_todo_ids = []
        for todo in summary_data["todos"]:
            title = str(todo.get("title", "")).strip()[:300]
            if not title:
                continue
            assignee = str(todo.get("assignee", "")).strip()[:80]
            status = todo.get("status") if todo.get("status") in {"open", "done", "dropped"} else "open"
            due_at = todo.get("due_at")
            todo_id = todo.get("todo_id")
            source_id = todo.get("source_message_id")
            if not isinstance(source_id, int) or source_id not in valid_source_ids:
                source_id = None
            existing = connection.execute(
                "SELECT id FROM todos WHERE session_id = ? AND id = ?",
                (session_id, todo_id),
            ).fetchone() if isinstance(todo_id, int) else None
            if not existing:
                existing = connection.execute(
                "SELECT id FROM todos WHERE session_id = ? AND title = ? COLLATE NOCASE AND status = 'open' ORDER BY id DESC LIMIT 1",
                (session_id, title),
                ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE todos SET title = ?, assignee = ?, status = ?, due_at = COALESCE(?, due_at), source_message_id = COALESCE(?, source_message_id), updated_at = ? WHERE id = ?",
                    (title, assignee, status, due_at, source_id, created_at, existing["id"]),
                )
                saved_todo_id = int(existing["id"])
            else:
                connection.execute(
                    "INSERT INTO todos (session_id, title, assignee, status, due_at, source_message_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, title, assignee, status, due_at, source_id, created_at, created_at),
                )
                saved_todo_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            touched_todo_ids.append(saved_todo_id)
    summary_data["version"] = version
    summary_data["through_message_id"] = latest_message
    summary_data["created_at"] = created_at
    return {
        "summary": summary_data,
        "ideas": saved_ideas,
        "todo_ids": list(dict.fromkeys(touched_todo_ids)),
        "message": _format_summary(summary_data, version, saved_ideas),
        "model": result["model"],
    }

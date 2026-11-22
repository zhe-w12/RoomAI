from __future__ import annotations

import json
import re
from io import BytesIO

from ..database import database
from .agent import complete_chat


def _cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _items(value, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if str(item).strip()][:limit]


def _average(dimensions: dict) -> float | None:
    values = [float(value) for value in dimensions.values() if isinstance(value, (int, float))]
    return round(sum(values) / len(values), 1) if values else None


async def generate_room_report(session_id: str, model: str, missing_dimensions: list[dict]) -> dict:
    with database() as connection:
        room = connection.execute("SELECT topic FROM rooms WHERE session_id = ?", (session_id,)).fetchone()
        participants = [dict(row) for row in connection.execute(
            """SELECT v.nickname, v.is_owner, COUNT(m.id) AS message_count
               FROM visitors v LEFT JOIN messages m ON m.session_id = v.session_id
                 AND m.visitor_id = v.visitor_id AND m.sender_type = 'user' AND m.is_recalled = 0
               WHERE v.session_id = ? GROUP BY v.visitor_id ORDER BY message_count DESC, v.rowid""",
            (session_id,),
        ).fetchall()]
        ideas = [dict(row) for row in connection.execute(
            """SELECT i.*,
                      (SELECT dimensions FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS dimensions,
                      (SELECT rationale FROM evaluations e WHERE e.idea_id = i.id ORDER BY e.id DESC LIMIT 1) AS rationale,
                      (SELECT COUNT(*) FROM idea_votes iv WHERE iv.idea_id = i.id AND iv.stance = 'support') AS support_count,
                      (SELECT COUNT(*) FROM idea_votes iv WHERE iv.idea_id = i.id AND iv.stance = 'oppose') AS oppose_count
               FROM ideas i WHERE i.session_id = ? ORDER BY i.updated_at DESC""",
            (session_id,),
        ).fetchall()]
        votes = [dict(row) for row in connection.execute(
            """SELECT v.id, v.question, v.status, v.message_id, v.created_at, v.closed_at, d.outcome
               FROM votes v LEFT JOIN decisions d ON d.source_vote_id = v.id
               WHERE v.session_id = ? ORDER BY v.id""",
            (session_id,),
        ).fetchall()]
        for vote in votes:
            vote["options"] = [dict(row) for row in connection.execute(
                """SELECT o.id, o.label, COUNT(r.id) AS votes FROM vote_options o
                   LEFT JOIN vote_records r ON r.option_id = o.id
                   WHERE o.vote_id = ? GROUP BY o.id ORDER BY o.id""",
                (vote["id"],),
            ).fetchall()]
        todos = [dict(row) for row in connection.execute(
            "SELECT title, assignee, status, due_at, source_message_id FROM todos WHERE session_id = ? ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, updated_at DESC",
            (session_id,),
        ).fetchall()]
        latest_summary = connection.execute(
            "SELECT content FROM summaries WHERE session_id = ? ORDER BY version DESC LIMIT 1", (session_id,)
        ).fetchone()
        recent_messages = [dict(row) for row in connection.execute(
            "SELECT id, sender_name, content, created_at FROM messages WHERE session_id = ? AND is_recalled = 0 ORDER BY id DESC LIMIT 100",
            (session_id,),
        ).fetchall()]
    if not room:
        raise ValueError("找不到这个房间")
    for idea in ideas:
        idea["dimensions"] = json.loads(idea["dimensions"] or "{}")
        idea["average_score"] = _average(idea["dimensions"])
    recent_messages.reverse()
    source_text = "\n".join(
        [str(item.get("content") or "") for item in recent_messages]
        + [str(item.get("title") or "") for item in todos]
        + [str(item.get("due_at") or "") for item in todos]
    )
    facts = {
        "topic": room["topic"],
        "participants": participants,
        "ideas": ideas,
        "votes": votes,
        "todos": todos,
        "latest_summary": json.loads(latest_summary["content"]) if latest_summary else None,
        "missing_dimensions": missing_dimensions,
        "recent_messages": recent_messages,
    }
    prompt = [
        {
            "role": "system",
            "content": (
                "你是 RoomAI 立项纪要与讨论复盘撰写员。只能依据输入事实，不得编造决定、用户研究、期限或数据。"
                "从 ideas 里选择推荐方案时只能返回已有 idea id；证据不足就返回 null 并说明待验证。"
                "风险需包含已讨论证据与建议应对，未验证假设和下次讨论建议应具体。只返回 JSON："
                '{"recommended_idea_id":null,"recommendation_reason":"...","risks":[{"risk":"...","evidence":"...","mitigation":"..."}],'
                '"assumptions":["..."],"next_discussion":["..."],"timeline":[{"milestone":"...","target":"...","basis":"..."}],"retrospective":"...","mermaid":""}'
                "timeline 只可整理消息或明确待办中有证据的里程碑；basis 必须摘自输入原文。没有证据就返回空数组。"
                "target 只有在原文明确给出日期/周期时才填写原文，否则写‘待定’，不得推算或编造时间。"
            ),
        },
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
    ]
    result = await complete_chat(prompt, model=model, session_id=session_id, json_mode=True, temperature=0.2)
    generated: dict = {}
    if result:
        try:
            parsed = json.loads(result["content"])
            if isinstance(parsed, dict):
                generated = parsed
        except (json.JSONDecodeError, TypeError):
            generated = {}
    idea_ids = {int(item["id"]) for item in ideas}
    recommended_id = generated.get("recommended_idea_id")
    if not isinstance(recommended_id, int) or recommended_id not in idea_ids:
        recommended_id = None
    recommendation = str(generated.get("recommendation_reason", "")).strip()[:1600]
    risks = generated.get("risks") if isinstance(generated.get("risks"), list) else []
    risks = [item for item in risks[:8] if isinstance(item, dict) and str(item.get("risk", "")).strip()]
    assumptions = _items(generated.get("assumptions"), 12)
    next_discussion = _items(generated.get("next_discussion"), 8)
    timeline = []
    raw_timeline = generated.get("timeline") if isinstance(generated.get("timeline"), list) else []
    compact_source = re.sub(r"[^\w\u4e00-\u9fff]+", "", source_text.casefold())
    for item in raw_timeline[:8]:
        if not isinstance(item, dict):
            continue
        milestone = str(item.get("milestone", "")).strip()[:120]
        basis = str(item.get("basis", "")).strip()[:260]
        compact_basis = re.sub(r"[^\w\u4e00-\u9fff]+", "", basis.casefold())
        if not milestone or len(compact_basis) < 5 or compact_basis not in compact_source:
            continue
        target = str(item.get("target", "待定")).strip()[:80] or "待定"
        compact_target = re.sub(r"[^\w\u4e00-\u9fff]+", "", target.casefold())
        if target != "待定" and compact_target not in compact_source:
            target = "待定"
        timeline.append({"milestone": milestone, "target": target, "basis": basis})
    retrospective = str(generated.get("retrospective", "")).strip()[:3000]
    diagram = str(generated.get("mermaid", "")).strip()[:6000]
    if not re.fullmatch(r"(?:flowchart|graph|mindmap|sequenceDiagram|classDiagram|stateDiagram-v2)[^\n]*[\s\S]*", diagram):
        diagram = ""

    total_messages = sum(int(item["message_count"]) for item in participants)
    minutes = [f"# RoomAI 立项纪要：{room['topic']}", "", "## 1. 讨论主题与参与人", f"讨论主题：{room['topic']}", "", "| 参与人 | 身份 | 发言数 |", "|---|---|---:|"]
    minutes.extend(f"| {_cell(item['nickname'])} | {'房主' if item['is_owner'] else '成员'} | {item['message_count']} |" for item in participants)
    minutes.extend(["", "## 2. 想法排序与评估", "", "| 想法 | 提出人 | 六维均分 | 支持 / 反对 | 状态 | 来源 |", "|---|---|---:|---:|---|---|"])
    ranked = sorted(ideas, key=lambda item: (item["average_score"] is not None, item["average_score"] or 0, item["support_count"], -item["oppose_count"]), reverse=True)
    if ranked:
        for idea in ranked:
            score = f"{idea['average_score']:.1f}/5" if idea["average_score"] is not None else "待评估"
            source = f"[消息 #{idea['source_message_id']}](/room/{session_id}#message-{idea['source_message_id']})" if idea["source_message_id"] else "—"
            minutes.append(f"| {_cell(idea['title'])} | {_cell(idea['proposer_name']) or '未记录'} | {score} | {idea['support_count']} / {idea['oppose_count']} | {_cell(idea['status'])} | {source} |")
    else:
        minutes.append("| 暂无已记录想法 | — | — | — | — | — |")
    chosen = next((item for item in ideas if item["id"] == recommended_id), None)
    minutes.extend(["", "## 3. 推荐方案及原因", ""])
    if chosen:
        minutes.append(f"推荐：**{_cell(chosen['title'])}**。{recommendation or '目前资料不足以给出更具体的推荐依据。'}")
    else:
        minutes.append(recommendation or "目前讨论记录不足以确认推荐方案；建议先验证关键假设后再决定。")
    minutes.extend(["", "## 4. 风险清单与应对", ""])
    if risks:
        for index, item in enumerate(risks, 1):
            minutes.append(f"{index}. **{_cell(item.get('risk'))}**。依据：{_cell(item.get('evidence')) or '讨论记录未提供更多依据'} 应对：{_cell(item.get('mitigation')) or '待团队补充'}")
    else:
        minutes.append("讨论中尚未形成结构化风险清单，建议结合未验证假设继续审查。")
    minutes.extend(["", "## 5. 下一步待办", "", "| 待办 | 负责人 | 截止日期 | 状态 | 来源 |", "|---|---|---|---|---|"])
    if todos:
        for item in todos:
            source = f"[消息 #{item['source_message_id']}](/room/{session_id}#message-{item['source_message_id']})" if item["source_message_id"] else "—"
            minutes.append(f"| {_cell(item['title'])} | {_cell(item['assignee']) or '待分配'} | {_cell(item['due_at']) or '未设定'} | {_cell(item['status'])} | {source} |")
    else:
        minutes.append("| 暂无明确待办 | — | — | — | — |")
    minutes.extend(["", "## 6. 分歧与投票结果", ""])
    if votes:
        for vote in votes:
            counts = "；".join(f"{_cell(option['label'])}：{option['votes']} 票" for option in vote["options"])
            minutes.append(f"- **{_cell(vote['question'])}**：{counts or '暂无投票'}。结论：{_cell(vote['outcome']) or '尚未结束'}。来源：[消息 #{vote['message_id']}](/room/{session_id}#message-{vote['message_id']})")
    else:
        minutes.append("本次讨论没有已记录的投票。")
    if missing_dimensions:
        minutes.extend(["", "## 7. 未覆盖维度", ""])
        minutes.extend(f"- **{_cell(item.get('label'))}**：{_cell(item.get('question'))}" for item in missing_dimensions)
    else:
        minutes.extend(["", "## 7. 未覆盖维度", "", "本次检查未发现明显缺失的讨论维度。"])
    minutes.extend(["", "## 8. 项目时间线（草案）", "", "| 里程碑 | 时间 | 讨论依据 |", "|---|---|---|"])
    if timeline:
        minutes.extend(f"| {_cell(item['milestone'])} | {_cell(item['target'])} | {_cell(item['basis'])} |" for item in timeline)
    else:
        minutes.append("| 暂无可验证的里程碑 | 待定 | 建议在后续讨论中明确阶段与时间 |")
    if diagram:
        minutes.extend(["", "## 9. 方案可视化", "", "```mermaid", diagram, "```"])
    minutes_markdown = "\n".join(minutes)

    retro = ["# 讨论复盘报告", "", "## 参与度", f"本次记录到 {total_messages} 条成员发言，参与人数 {len(participants)}。"]
    silent = [item["nickname"] for item in participants if int(item["message_count"]) == 0]
    retro.append("没有发言的成员：" + ("、".join(silent) if silent else "无。"))
    retro.extend(["", "## 想法与决策统计", f"- 记录想法：{len(ideas)} 个；已采纳：{sum(item['status'] in {'adopted', 'accepted'} for item in ideas)} 个；已否决：{sum(item['status'] in {'rejected', 'declined'} for item in ideas)} 个。", f"- 投票：{len(votes)} 轮；完成：{sum(item['status'] == 'closed' for item in votes)} 轮。", "", "## 未验证假设"])
    retro.extend(f"- {item}" for item in assumptions or ["讨论记录中尚未整理出明确的未验证假设。"])
    retro.extend(["", "## 下次讨论建议"])
    retro.extend(f"- {item}" for item in next_discussion or ["围绕未覆盖维度和推荐方案中的关键假设继续收集证据。"])
    if retrospective:
        retro.extend(["", "## 复盘说明", retrospective])
    return {"minutes_markdown": minutes_markdown, "retrospective_markdown": "\n".join(retro)}


def markdown_to_docx(markdown: str) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    normal = document.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10)
    lines = markdown.splitlines()
    index = 0
    in_code = False
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("```"):
            in_code = not in_code
            index += 1
            continue
        if not line:
            index += 1
            continue
        if not in_code and line.startswith("|"):
            table_rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if cells and not all(re.fullmatch(r"[-: ]+", cell or "-") for cell in cells):
                    table_rows.append(cells)
                index += 1
            if table_rows:
                columns = max(len(row) for row in table_rows)
                table = document.add_table(rows=len(table_rows), cols=columns)
                table.style = "Table Grid"
                for row_index, values in enumerate(table_rows):
                    for col_index in range(columns):
                        table.cell(row_index, col_index).text = values[col_index] if col_index < len(values) else ""
            continue
        if line.startswith("# "):
            paragraph = document.add_heading(line[2:], level=0)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        elif line.startswith("## "):
            document.add_heading(line[3:], level=1)
        elif line.startswith("### "):
            document.add_heading(line[4:], level=2)
        elif line.startswith("- "):
            document.add_paragraph(line[2:], style="List Bullet")
        elif re.match(r"^\d+\.\s", line):
            document.add_paragraph(re.sub(r"^\d+\.\s", "", line), style="List Number")
        else:
            clean = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
            document.add_paragraph(clean)
        index += 1
    output = BytesIO()
    document.save(output)
    return output.getvalue()

from __future__ import annotations

import html
import json
import re
from io import BytesIO

from ..database import database
from .agent import complete_chat
from .diagram import sanitize_mermaid_code

# 纪要里的状态一律写中文，不要把数据库里的英文枚举值直接打出来。
IDEA_STATUS_LABELS = {"considering": "讨论中", "pending": "待定", "adopted": "已采纳", "accepted": "已采纳", "rejected": "已否决", "declined": "已否决"}
TODO_STATUS_LABELS = {"open": "进行中", "done": "已完成", "dropped": "已放弃"}


def _cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _items(value, limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if str(item).strip()][:limit]


def _average(dimensions: dict) -> float | None:
    values = [float(value) for value in dimensions.values() if isinstance(value, (int, float))]
    return round(sum(values) / len(values), 1) if values else None


def _render_project_proposal(
    topic: str,
    data: dict,
    chosen: dict | None,
    risks: list[dict],
    timeline: list[dict],
    todos: list[dict],
    missing_dimensions: list[dict],
) -> str:
    text = lambda key, limit=1400: str(data.get(key, "")).strip()[:limit]
    bullets = lambda key, limit=8: _items(data.get(key), limit)
    lines = [f"# 立项书：{_cell(topic)}", "", "> 根据本次讨论记录草拟；建议指标与待确认项需由团队最终确认。", ""]
    sections = [
        ("1. 项目背景", text("background")),
        ("2. 要解决的问题", text("problem")),
    ]
    for heading, content in sections:
        lines.extend([f"## {heading}", "", _cell(content) if content else "讨论记录中尚未形成明确结论，待团队补充。", ""])
    for heading, key in (("3. 项目目标", "goals"), ("4. 目标用户", "target_users"), ("5. 项目范围", "scope")):
        values = bullets(key)
        lines.extend([f"## {heading}", ""])
        lines.extend(f"- {_cell(value)}" for value in values)
        if not values:
            lines.append("- 待团队确认")
        lines.append("")
    lines.extend(["## 6. 方案概述", "", _cell(text("solution")) if text("solution") else "待团队根据讨论结果补充。", ""])
    lines.extend(["## 7. 推荐方向", ""])
    if chosen:
        lines.append(f"建议优先验证「{_cell(chosen.get('title'))}」。{_cell(text('recommendation_reason', 1000)) or '讨论记录尚不足以确认最终方案。'}")
    else:
        lines.append("讨论尚未确认推荐方案；请结合候选想法和验证结果决定。")
    lines.extend(["", "## 8. 实施计划", "", "| 里程碑 | 时间 | 讨论依据 |", "|---|---|---|"])
    if timeline:
        lines.extend(f"| {_cell(item['milestone'])} | {_cell(item['target'])} | {_cell(item['basis'])} |" for item in timeline)
    else:
        lines.append("| 待团队确定 | 待定 | 讨论记录中没有可核实的时间安排 |")
    lines.extend(["", "## 9. 资源与分工", ""])
    resources = bullets("resources")
    if resources:
        lines.extend(f"- {_cell(item)}" for item in resources)
    else:
        lines.append("- 人力与预算：待团队确认")
    if todos:
        lines.extend(["", "已记录的下一步："])
        lines.extend(f"- {_cell(item['title'])}（负责人：{_cell(item['assignee']) or '待分配'}；期限：{_cell(item['due_at']) or '待定'}）" for item in todos[:12])
    lines.extend(["", "## 10. 风险与应对", ""])
    if risks:
        lines.extend(f"- **{_cell(item.get('risk'))}**；依据：{_cell(item.get('evidence')) or '讨论记录未提供'}；应对：{_cell(item.get('mitigation')) or '待团队补充'}" for item in risks)
    else:
        lines.append("讨论记录中尚无结构化风险项，建议在实施前补充风险评估。")
    metrics = bullets("success_metrics")
    lines.extend(["", "## 11. 建议验证指标", ""])
    lines.extend(f"- {_cell(item)}（建议项，待团队确认）" for item in metrics)
    if not metrics:
        lines.append("讨论记录中尚未明确量化指标，待团队确认。")
    questions = bullets("open_questions", 10)
    questions.extend(_cell(item.get("question")) for item in missing_dimensions if item.get("question"))
    lines.extend(["", "## 12. 待确认事项", ""])
    lines.extend(f"- {_cell(item)}" for item in dict.fromkeys(questions))
    if not questions:
        lines.append("暂无额外待确认事项。")
    return "\n".join(lines)


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
            "SELECT id, sender_name, sender_type, content, created_at FROM messages WHERE session_id = ? AND is_recalled = 0 ORDER BY id DESC LIMIT 100",
            (session_id,),
        ).fetchall()]
    if not room:
        raise ValueError("找不到这个房间")
    for idea in ideas:
        idea["dimensions"] = json.loads(idea["dimensions"] or "{}")
        idea["average_score"] = _average(idea["dimensions"])
    recent_messages.reverse()
    source_text = "\n".join(
        [str(item.get("content") or "") for item in recent_messages if item.get("sender_type") == "user"]
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
                '"assumptions":["..."],"next_discussion":["..."],"timeline":[{"milestone":"...","target":"...","basis":"..."}],'
                '"proposal":{"background":"...","problem":"...","goals":["..."],"target_users":["..."],"scope":["..."],'
                '"solution":"...","resources":["..."],"success_metrics":["..."],"open_questions":["..."]},"retrospective":"...","mermaid":""}'
                "timeline 只可整理消息或明确待办中有证据的里程碑；basis 必须摘自输入原文。没有证据就返回空数组。"
                "target 只有在原文明确给出日期/周期时才填写原文，否则写‘待定’，不得推算或编造时间。"
                "proposal 是面向团队评审的立项书草稿：背景、问题、目标用户和已确认方案必须来自输入事实；"
                "success_metrics 只能写成待确认的建议指标，不得冒充团队已达成的承诺；资源或范围缺少证据时返回空值。"
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
    proposal_data = generated.get("proposal") if isinstance(generated.get("proposal"), dict) else {}
    proposal_data["recommendation_reason"] = recommendation
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
    diagram = sanitize_mermaid_code(str(generated.get("mermaid", "")))

    total_messages = sum(int(item["message_count"]) for item in participants)
    minutes = [f"# RoomAI 立项纪要：{room['topic']}", "", "## 1. 讨论主题与参与人", f"讨论主题：{room['topic']}", "", "| 参与人 | 身份 | 发言数 |", "|---|---|---:|"]
    minutes.extend(f"| {_cell(item['nickname'])} | {'房主' if item['is_owner'] else '成员'} | {item['message_count']} |" for item in participants)
    minutes.extend(["", "## 2. 想法排序与评估", "", "| 想法 | 提出人 | 六维均分 | 支持 / 反对 | 状态 | 来源 |", "|---|---|---:|---:|---|---|"])
    ranked = sorted(ideas, key=lambda item: (item["average_score"] is not None, item["average_score"] or 0, item["support_count"], -item["oppose_count"]), reverse=True)
    if ranked:
        for idea in ranked:
            score = f"{idea['average_score']:.1f}/5" if idea["average_score"] is not None else "待评估"
            source = f"[消息 #{idea['source_message_id']}](/room/{session_id}#message-{idea['source_message_id']})" if idea["source_message_id"] else "—"
            minutes.append(f"| {_cell(idea['title'])} | {_cell(idea['proposer_name']) or '未记录'} | {score} | {idea['support_count']} / {idea['oppose_count']} | {_cell(IDEA_STATUS_LABELS.get(idea['status'], idea['status']))} | {source} |")
    else:
        minutes.append("| 暂无已记录想法 | — | — | — | — | — |")
    chosen = next((item for item in ideas if item["id"] == recommended_id), None)
    proposal_markdown = _render_project_proposal(
        room["topic"], proposal_data, chosen, risks, timeline, todos, missing_dimensions
    )
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
            minutes.append(f"| {_cell(item['title'])} | {_cell(item['assignee']) or '待分配'} | {_cell(item['due_at']) or '未设定'} | {_cell(TODO_STATUS_LABELS.get(item['status'], item['status']))} | {source} |")
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
    return {
        "minutes_markdown": minutes_markdown,
        "proposal_markdown": proposal_markdown,
        "retrospective_markdown": "\n".join(retro),
    }


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


# PDF 导出的页面样式：A4，中文字体优先用 Noto Sans CJK SC（Linux 服务器常见），
# 其次微软雅黑（Windows / 已装该字体的环境），再退回系统无衬线字体。
# 字体缺失时 WeasyPrint 会退回默认字体导致中文变方框，部署时需确认字体已装。
_PDF_CSS = """
@page {
  size: A4;
  margin: 2cm 1.8cm;
  @bottom-center { content: "第 " counter(page) " 页 / 共 " counter(pages) " 页"; font-size: 8pt; color: #9db1c7; }
}
body { font-family: "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 11pt; line-height: 1.75; color: #10233f; }
h1 { font-size: 20pt; color: #1d4ed8; border-bottom: 2px solid #2563eb; padding-bottom: 8px; margin: 0 0 0.6em; }
h2 { font-size: 14pt; color: #1d4ed8; margin: 1.4em 0 0.5em; }
h3 { font-size: 12pt; color: #10233f; margin: 1.2em 0 0.4em; }
p { margin: 0.4em 0; }
ul, ol { margin: 0.4em 0; padding-left: 1.4em; }
li { margin: 0.2em 0; }
hr { border: none; border-top: 1px solid #dce8f5; margin: 1.6em 0; }
table { border-collapse: collapse; width: 100%; margin: 0.8em 0; font-size: 10pt; }
th, td { border: 1px solid #dce8f5; padding: 6px 8px; text-align: left; }
th { background: #eff6ff; color: #1d4ed8; }
pre { background: #f5f9fd; border: 1px solid #e9f1f9; border-radius: 6px; padding: 10px 12px; font-size: 9pt; line-height: 1.6; white-space: pre-wrap; word-break: break-all; }
pre .lang { display: block; color: #6b8299; font-size: 8pt; margin-bottom: 4px; }
code { font-family: "JetBrains Mono", "Noto Sans Mono CJK SC", monospace; font-size: 0.9em; background: #f5f9fd; padding: 1px 4px; border-radius: 4px; }
pre code { background: none; padding: 0; }
"""


def _inline_html(text: str) -> str:
    """行内格式：先转义，再把 **粗体** 和 `代码` 换成标签。"""
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _markdown_to_html_body(markdown: str) -> str:
    """行式 Markdown 转 HTML 片段，结构与 markdown_to_docx 对齐（标题、表格、
    列表、代码块、分隔线）。代码块在 Word 导出里是跳过的，PDF 里保留为 <pre>，
    否则纪要里的方案图（mermaid 源码）会整段丢失。"""
    lines = markdown.splitlines()
    parts: list[str] = []
    index = 0
    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        if line.startswith("```"):
            language = line[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            index += 1  # 跳过结尾的 ```
            label = f'<span class="lang">{html.escape(language)}</span>' if language else ""
            parts.append(f"<pre>{label}<code>{html.escape(chr(10).join(code_lines))}</code></pre>")
            continue
        if not line:
            index += 1
            continue
        if line in ("---", "***", "___"):
            parts.append("<hr>")
            index += 1
            continue
        if line.startswith("|"):
            table_rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if cells and not all(re.fullmatch(r"[-: ]+", cell or "-") for cell in cells):
                    table_rows.append(cells)
                index += 1
            if table_rows:
                rows_html = []
                for row_index, values in enumerate(table_rows):
                    tag = "th" if row_index == 0 else "td"
                    cells_html = "".join(f"<{tag}>{_inline_html(v)}</{tag}>" for v in values)
                    rows_html.append(f"<tr>{cells_html}</tr>")
                parts.append(f"<table>{''.join(rows_html)}</table>")
            continue
        if line.startswith("# "):
            parts.append(f"<h1>{_inline_html(line[2:])}</h1>")
        elif line.startswith("## "):
            parts.append(f"<h2>{_inline_html(line[3:])}</h2>")
        elif line.startswith("### "):
            parts.append(f"<h3>{_inline_html(line[4:])}</h3>")
        elif line.startswith("- "):
            items: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                items.append(f"<li>{_inline_html(lines[index].strip()[2:])}</li>")
                index += 1
            parts.append(f"<ul>{''.join(items)}</ul>")
            continue
        elif re.match(r"^\d+\.\s", line):
            items = []
            while index < len(lines) and re.match(r"^\d+\.\s", lines[index].strip()):
                items.append(f"<li>{_inline_html(re.sub(r'^\\d+\\.\\s', '', lines[index].strip()))}</li>")
                index += 1
            parts.append(f"<ol>{''.join(items)}</ol>")
            continue
        else:
            parts.append(f"<p>{_inline_html(line)}</p>")
        index += 1
    return "\n".join(parts)


def markdown_to_pdf(markdown: str) -> bytes:
    """纪要 Markdown 转 PDF（WeasyPrint + HTML 模板）。weasyprint 延迟导入：
    没装时抛 ImportError，由调用方转成 501；且不拖慢后端启动。"""
    from weasyprint import HTML

    document = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<style>{_PDF_CSS}</style></head><body>"
        f"{_markdown_to_html_body(markdown)}</body></html>"
    )
    output = BytesIO()
    HTML(string=document).write_pdf(output)
    return output.getvalue()

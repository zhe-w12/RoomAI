import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Download, FileText, Printer } from "lucide-react";
import { useParams } from "react-router-dom";
import MarkdownContent from "../components/MarkdownContent";
import { api, loadIdentity, RoomReport } from "../lib/api";

export default function ReportPage() {
  const { sessionId = "" } = useParams();
  const identity = useMemo(() => loadIdentity(sessionId), [sessionId]);
  const [report, setReport] = useState<RoomReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let canceled = false;
    if (!identity) {
      setError("当前浏览器没有这个房间的身份记录，请先加入房间后再查看纪要。");
      setLoading(false);
      return;
    }
    api.getRoomReport(sessionId, identity.visitor_id)
      .then((value) => { if (!canceled) setReport(value); })
      .catch((reason: unknown) => { if (!canceled) setError(reason instanceof Error ? reason.message : "暂时无法读取立项纪要"); })
      .finally(() => { if (!canceled) setLoading(false); });
    return () => { canceled = true; };
  }, [identity, sessionId]);

  return <main className="report-page">
    <header className="report-top no-print">
      <a className="report-back" href={`/room/${sessionId}`}><ArrowLeft size={15} />返回讨论</a>
      <nav className="report-actions" aria-label="纪要导出">
        {identity && <>
          <a className="btn" href={api.roomReportExportUrl(sessionId, identity.visitor_id, "md")}><Download size={14} />Markdown</a>
          <a className="btn" href={api.roomReportExportUrl(sessionId, identity.visitor_id, "docx")}><FileText size={14} />Word</a>
        </>}
        <button className="btn btn-primary" onClick={() => window.print()}><Printer size={14} />打印 / 导出 PDF</button>
      </nav>
    </header>
    <article className="report-content">
      {loading ? <div className="report-state">正在打开立项纪要…</div> : error ? <div className="report-state report-error">{error}</div> : report ? <>
        <p className="eyebrow">ROOMAI · 讨论成果</p>
        <MarkdownContent content={report.minutes_markdown} />
        <hr className="report-divider" />
        <MarkdownContent content={report.retrospective_markdown} />
        <p className="report-date">生成时间：{new Intl.DateTimeFormat("zh-CN", { dateStyle: "long", timeStyle: "short" }).format(new Date(report.created_at))}</p>
      </> : null}
    </article>
  </main>;
}

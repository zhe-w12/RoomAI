import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Activity, ArrowLeft, ArrowRight, CheckCircle2, Clock3, Lightbulb, ListTodo, MessageCircle, RefreshCw, Sparkles, Vote as VoteIcon } from "lucide-react";
import { api, loadIdentity, ReplayEvent } from "../lib/api";

const eventMeta: Record<ReplayEvent["type"], { label: string; icon: typeof MessageCircle; color: string }> = {
  message: { label: "发言", icon: MessageCircle, color: "replay-blue" },
  summary: { label: "总结", icon: Sparkles, color: "replay-violet" },
  idea: { label: "想法", icon: Lightbulb, color: "replay-amber" },
  idea_status: { label: "状态", icon: RefreshCw, color: "replay-green" },
  evaluation: { label: "评估", icon: Activity, color: "replay-green" },
  vote: { label: "投票", icon: VoteIcon, color: "replay-violet" },
  decision: { label: "决策", icon: CheckCircle2, color: "replay-green" },
  todo: { label: "待办", icon: ListTodo, color: "replay-amber" },
};

function eventTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export default function ReplayPage() {
  const { sessionId = "" } = useParams();
  const [topic, setTopic] = useState("");
  const [events, setEvents] = useState<ReplayEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const identity = loadIdentity(sessionId);

  useEffect(() => {
    let canceled = false;
    if (!identity) { setLoading(false); return; }
    api.getReplay(sessionId, identity.visitor_id).then((data) => {
      if (canceled) return;
      setTopic(data.topic);
      setEvents(data.events);
    }).catch((err: unknown) => {
      if (!canceled) setError(err instanceof Error ? err.message : "暂时无法读取讨论回放");
    }).finally(() => { if (!canceled) setLoading(false); });
    return () => { canceled = true; };
  }, [sessionId]);

  return <main className="replay-page">
    <header className="replay-header">
      <a className="replay-back" href={`/room/${sessionId}`}><ArrowLeft size={16} />返回讨论</a>
      <div className="replay-heading"><span><Clock3 size={14} />ROOMAI REPLAY</span><h1>{topic || "讨论回放"}</h1><p>沿时间顺序回顾消息、想法、评估、投票和决策。</p></div>
    </header>
    <section className="replay-content">
      {loading ? <div className="replay-empty">正在整理时间轴…</div> : error ? <div className="replay-empty replay-error">{error}</div> : events.length === 0 ? <div className="replay-empty">房间里还没有可回放的活动。</div> : <div className="replay-timeline">
        {events.map((item) => {
          const meta = eventMeta[item.type];
          const Icon = meta.icon;
          return <article className="replay-event" key={item.id}>
            <span className={`replay-event-icon ${meta.color}`}><Icon size={15} /></span>
            <div className="replay-event-card">
              <header><span>{meta.label}</span><time>{eventTime(item.created_at)}</time></header>
              <h2>{item.title}</h2>
              {item.detail && <p>{item.detail}</p>}
              {item.message_id && <a href={`/room/${sessionId}#message-${item.message_id}`}>跳到讨论位置 <ArrowRight size={12} /></a>}
            </div>
          </article>;
        })}
      </div>}
    </section>
  </main>;
}

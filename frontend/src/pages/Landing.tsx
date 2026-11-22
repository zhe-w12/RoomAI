import { FormEvent, useState } from "react";
import { ArrowDownRight, ArrowRight, ArrowUpRight, Check, ChevronRight, CircleHelp, Command, Lightbulb, MessageCircle, Plus, Sparkles, Users, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, saveIdentity } from "../lib/api";

type Mode = "create" | "join" | null;

export default function LandingPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>(null);
  const [topic, setTopic] = useState("");
  const [nickname, setNickname] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const identity = mode === "create"
        ? await api.createRoom(topic.trim(), nickname.trim())
        : await api.joinByCode(inviteCode.trim(), nickname.trim());
      saveIdentity(identity);
      navigate(`/room/${identity.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "连接失败，请检查服务是否已启动");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="landing-shell">
      <nav className="landing-nav">
        <a className="brand" href="/" aria-label="RoomAI 首页">
          <span className="brand-mark"><span /><span /><span /></span>
          <span>room<span className="brand-ai">ai</span></span>
        </a>
        <div className="nav-note"><span className="nav-dot" /> 为团队的下一步而聊</div>
        <a className="nav-about" href="#how-it-works">怎么开始 <ArrowDownRight size={15} /></a>
      </nav>

      <section className="landing-hero">
        <div className="hero-copy">
          <p className="eyebrow"><Sparkles size={14} /> 团队立项前的想法讨论室</p>
          <h1>把一个好想法，<br /><span>聊成下一步。</span></h1>
          <p className="hero-description">邀请伙伴进来一起聊。RoomAI 会跟进讨论、梳理共识，帮你们把想法变成可以开始做的事。</p>
          <div className="hero-actions">
            <button className="button button-primary button-large" onClick={() => { setMode("create"); setError(""); }}>
              <Plus size={18} /> 创建讨论房间 <ArrowRight size={16} />
            </button>
            <button className="button button-secondary button-large" onClick={() => { setMode("join"); setError(""); }}>
              加入已有房间 <ArrowUpRight size={16} />
            </button>
          </div>
          <div className="trust-row"><span className="avatar-stack"><i>林</i><i>周</i><i>陈</i></span><span>无需注册 · 一个链接就能开始</span></div>
        </div>

        <div className="hero-art" aria-label="RoomAI 讨论示意">
          <div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" />
          <div className="art-card art-card-main">
            <div className="art-card-top"><span className="art-room-icon"><MessageCircle size={15} /></span><span>团队讨论室</span><span className="art-live"><i /> 实时</span></div>
            <div className="art-topic">校园二手物品<br />怎么流转起来？</div>
            <div className="art-divider" />
            <div className="art-message"><span className="mini-avatar mini-lin">林</span><p>可以先从毕业季的闲置开始，需求最集中。</p></div>
            <div className="art-message art-message-ai"><span className="mini-ai"><Sparkles size={13} /></span><p>这个切入点很清楚。大家觉得先做校内小程序还是社群？</p></div>
            <div className="art-thinking"><span /><span /><span /> RoomAI 正在跟进</div>
          </div>
          <div className="art-float art-float-idea"><span><Lightbulb size={15} /></span><div><b>一个新想法</b><small>宿舍楼下设置交换架</small></div><span className="idea-score">4.6</span></div>
          <div className="art-float art-float-people"><span className="tiny-person">林</span><span className="tiny-person person-blue">周</span><span className="tiny-person person-lilac">陈</span><b>3 人正在讨论</b></div>
          <div className="art-scribble">ideas → action</div>
        </div>
      </section>

      <section id="how-it-works" className="how-section">
        <div className="how-heading"><span>简单开始，认真聊完</span><span>01 — 03</span></div>
        <div className="how-grid">
          <article><span className="step-number">01</span><Users size={19} /><h3>定一个主题</h3><p>写下你们想解决的问题，邀请团队伙伴加入。</p><ChevronRight className="step-arrow" size={17} /></article>
          <article><span className="step-number">02</span><MessageCircle size={19} /><h3>一起把想法聊透</h3><p>自由讨论、引用回复，图片和草图也能放进来。</p><ChevronRight className="step-arrow" size={17} /></article>
          <article><span className="step-number">03</span><Check size={19} /><h3>带着共识继续</h3><p>沉淀值得做的方向、需要回答的问题和下一步。</p><CircleHelp className="step-arrow" size={17} /></article>
        </div>
      </section>

      <footer className="landing-footer"><span>RoomAI</span><span>让每一次讨论，都更接近行动。</span><span><Command size={14} /> 一个轻量的讨论空间</span></footer>

      {mode && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setMode(null); }}>
        <form className="room-modal" onSubmit={submit}>
          <button type="button" className="icon-button modal-close" onClick={() => setMode(null)} aria-label="关闭"><X size={18} /></button>
          <div className="modal-icon">{mode === "create" ? <Plus size={20} /> : <ArrowUpRight size={20} />}</div>
          <p className="eyebrow">{mode === "create" ? "新建一个讨论空间" : "欢迎回到讨论"}</p>
          <h2>{mode === "create" ? "这次想聊什么？" : "加入团队房间"}</h2>
          <p className="modal-subtitle">{mode === "create" ? "定好主题，RoomAI 会帮你们从第一句话开始。" : "输入伙伴分享的 6 位邀请码，马上加入。"}</p>
          {mode === "create" ? <label className="field-label">讨论主题<input autoFocus required minLength={2} maxLength={240} value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="例如：为校园社团做一款活动管理工具" /></label> : <label className="field-label">6 位邀请码<input autoFocus required inputMode="numeric" maxLength={6} value={inviteCode} onChange={(event) => setInviteCode(event.target.value.replace(/\D/g, ""))} placeholder="000000" className="invite-field" /></label>}
          <label className="field-label">你的昵称<input required maxLength={32} value={nickname} onChange={(event) => setNickname(event.target.value)} placeholder="伙伴们怎么称呼你？" /></label>
          {error && <p className="form-error">{error}</p>}
          <button className="button button-primary button-submit" disabled={busy}>{busy ? <><span className="button-spinner" /> 正在准备房间…</> : <>{mode === "create" ? "创建讨论房间" : "加入房间"} <ArrowRight size={17} /></>}</button>
          <p className="modal-footnote">无需注册，昵称只在这个房间里使用。</p>
        </form>
      </div>}
    </main>
  );
}

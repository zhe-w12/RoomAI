import { FormEvent, useEffect, useState } from "react";
import { ArrowRight, ArrowUpRight, Check, Cpu, FileText, History, ImagePlus, Lightbulb, Plus, Vote, X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api, RoomTemplate, saveIdentity } from "../lib/api";
import { memberColor } from "../lib/memberColor";
import BrandMark from "../components/BrandMark";

type Mode = "create" | "join" | null;

export default function LandingPage() {
  const navigate = useNavigate();
  const linColor = memberColor("林");
  const zhouColor = memberColor("周");
  const [mode, setMode] = useState<Mode>(null);
  const [topic, setTopic] = useState("");
  const [nickname, setNickname] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [templateId, setTemplateId] = useState("general");
  const [templates, setTemplates] = useState<RoomTemplate[]>([]);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api.getRoomTemplates().then((result) => setTemplates(result.templates)).catch(() => setTemplates([]));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const identity = mode === "create"
        ? await api.createRoom(topic.trim(), nickname.trim(), templateId, password)
        : await api.joinByCode(inviteCode.trim(), nickname.trim(), password);
      saveIdentity(identity);
      navigate(`/room/${identity.session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "连接失败，请检查服务是否已启动");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="landing">
      <nav className="landing-nav">
        <a className="brand" href="/" aria-label="RoomAI 首页">
          <BrandMark />
          <span>roomai</span>
        </a>
        <a className="nav-link" href="#how-it-works">怎么开始</a>
        <a className="nav-link" href="https://github.com/zhe-w12/RoomAI" target="_blank" rel="noreferrer">GitHub <ArrowUpRight size={13} /></a>
      </nav>

      <section className="hero">
        <p className="eyebrow">团队立项前的想法讨论室</p>
        <h1>把一个好想法，<br />聊成下一步。</h1>
        <p className="hero-sub">邀请伙伴进来一起聊。RoomAI 会跟进讨论、梳理共识，帮你们把想法变成可以开始做的事。</p>
        <div className="hero-actions">
          <button className="btn btn-primary" onClick={() => { setMode("create"); setError(""); }}>
            <Plus size={16} /> 创建讨论房间
          </button>
          <button className="btn" onClick={() => { setMode("join"); setError(""); }}>
            加入已有房间 <ArrowUpRight size={15} />
          </button>
        </div>
        <p className="hero-note">无需注册 · 一个链接就能开始</p>

        <div className="demo" aria-label="RoomAI 讨论示意">
          <div className="demo-head"><b>校园二手物品怎么流转起来？</b><span>3 人在线</span></div>
          <div className="demo-body">
            <div className="demo-msg"><span className="avatar avatar-sm" style={linColor}>林</span><div><b style={{ color: linColor.color }}>林</b><p>可以先从毕业季的闲置开始，需求最集中。</p></div></div>
            <div className="demo-msg"><span className="avatar avatar-sm" style={zhouColor}>周</span><div><b style={{ color: zhouColor.color }}>周</b><p>同意，毕业季大家都有东西要出，验证成本也低。</p></div></div>
            <div className="demo-msg demo-msg-ai"><span className="avatar avatar-sm avatar-ai"><BrandMark /></span><div><b>RoomAI</b><p>这个切入点很清楚：需求集中、验证成本低。我给它打了 4.6 分。大家觉得先做校内小程序还是社群？</p></div></div>
            <div className="demo-idea"><b>一个新想法</b><span>宿舍楼下设置交换架</span><span>4.6 / 5</span></div>
            <div className="demo-memo"><Check size={14} /><span><b>讨论结束时</b> 已形成共识：先做小程序 MVP · 下一步：林出一份问卷</span></div>
          </div>
        </div>
      </section>

      <section className="features" aria-label="RoomAI 能做什么">
        <div><span className="feature-icon"><History size={18} /></span><h3>自动总结</h3><p>聊完 1 分钟没人说话，自动整理共识、分歧和待办。</p></div>
        <div><span className="feature-icon"><Lightbulb size={18} /></span><h3>想法评分</h3><p>每个想法按六个维度打分，好的想法不会被淹没。</p></div>
        <div><span className="feature-icon"><Vote size={18} /></span><h3>投票表决</h3><p>有分歧就发起投票，票数实时同步，结果记进纪要。</p></div>
        <div><span className="feature-icon"><FileText size={18} /></span><h3>立项纪要</h3><p>讨论结束生成纪要、立项书草稿和复盘，可导出 Word / PDF。</p></div>
        <div><span className="feature-icon"><ImagePlus size={18} /></span><h3>图片理解</h3><p>草图、原型、竞品截图发进来，AI 能看懂并参与讨论。</p></div>
        <div><span className="feature-icon"><Cpu size={18} /></span><h3>自定义模型</h3><p>每个房间都能配自己的大模型地址、密钥和模型名。</p></div>
      </section>

      <section id="how-it-works" className="steps">
        <div><span className="step-num">01</span><h3>定一个主题</h3><p>写下你们想解决的问题，邀请团队伙伴加入。</p></div>
        <div><span className="step-num">02</span><h3>一起把想法聊透</h3><p>自由讨论、引用回复，图片和草图也能放进来。</p></div>
        <div><span className="step-num">03</span><h3>带着共识继续</h3><p>沉淀值得做的方向、需要回答的问题和下一步。</p></div>
      </section>

      <footer className="landing-footer"><span>RoomAI</span><span>让每一次讨论，都更接近行动。</span><a href="https://github.com/zhe-w12/RoomAI" target="_blank" rel="noreferrer">GitHub · MIT 开源</a></footer>

      {mode && <div className="overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) setMode(null); }}>
        <form className="modal" onSubmit={submit}>
          <button type="button" className="icon-btn modal-close" onClick={() => setMode(null)} aria-label="关闭"><X size={17} /></button>
          <p className="eyebrow">{mode === "create" ? "新建讨论" : "加入讨论"}</p>
          <h2>{mode === "create" ? "这次想聊什么？" : "加入团队房间"}</h2>
          <p className="modal-sub">{mode === "create" ? "定好主题，RoomAI 会帮你们从第一句话开始。" : "输入伙伴分享的 6 位邀请码，马上加入。"}</p>
          {mode === "create"
            ? <>
                <label className="field">讨论主题<input autoFocus required minLength={2} maxLength={240} value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="例如：为校园社团做一款活动管理工具" /></label>
                <label className="field">讨论模板<select value={templateId} onChange={(event) => setTemplateId(event.target.value)}>{templates.map((template) => <option value={template.id} key={template.id}>{template.label}</option>)}</select><small className="field-note">{templates.find((template) => template.id === templateId)?.description}</small></label>
                <label className="field">房间密码（可选）<input type="password" autoComplete="new-password" minLength={4} maxLength={72} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="设置后，加入房间需要输入密码" /><small className="field-note">留空时，收到邀请链接或邀请码的人都可以加入。</small></label>
              </>
            : <label className="field">6 位邀请码<input autoFocus required inputMode="numeric" maxLength={6} value={inviteCode} onChange={(event) => setInviteCode(event.target.value.replace(/\D/g, ""))} placeholder="000000" className="invite-field" /></label>}
          {mode === "join" && <label className="field">房间密码（如有）<input type="password" autoComplete="current-password" maxLength={72} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="房间设置了密码时填写" /></label>}
          <label className="field">你的昵称<input required maxLength={32} value={nickname} onChange={(event) => setNickname(event.target.value)} placeholder="伙伴们怎么称呼你？" /></label>
          {error && <p className="form-error">{error}</p>}
          <button className="btn btn-primary btn-block" disabled={busy}>{busy ? <><span className="btn-spinner" /> 正在准备房间…</> : <>{mode === "create" ? "创建讨论房间" : "加入房间"} <ArrowRight size={16} /></>}</button>
          <p className="modal-foot">无需注册，昵称只在这个房间里使用。</p>
        </form>
      </div>}
    </main>
  );
}

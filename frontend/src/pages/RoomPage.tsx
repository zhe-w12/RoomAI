import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Activity, AtSign, ArrowDown, ArrowRight, Check, ChevronDown, CircleHelp, Clipboard, ClipboardCheck, Command, FileImage, ImagePlus, Lightbulb, LoaderCircle, Menu, MessageCircle, MoreHorizontal, Paperclip, Plus, Search, Send, Settings2, Sparkles, Users, X } from "lucide-react";
import { api, Identity, loadIdentity, Member, Message, Room, saveIdentity } from "../lib/api";

const commands = ["/总结", "/评估", "/投票", "/画图", "/挑战", "/待办", "/结束", "/帮助"];

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function avatarColor(name: string) {
  const colors = ["avatar-coral", "avatar-blue", "avatar-violet", "avatar-green", "avatar-amber"];
  const index = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % colors.length;
  return colors[index];
}

export default function RoomPage() {
  const { sessionId = "" } = useParams();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [unreadMentionIds, setUnreadMentionIds] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [roomError, setRoomError] = useState("");
  const [onlineIds, setOnlineIds] = useState<string[]>([]);
  const [typingNames, setTypingNames] = useState<string[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [newMessages, setNewMessages] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [composerValue, setComposerValue] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [linkCopied, setLinkCopied] = useState(false);
  const [joinName, setJoinName] = useState("");
  const [joining, setJoining] = useState(false);
  const [nearBottom, setNearBottom] = useState(true);
  const [selectedImage, setSelectedImage] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState("");
  const [quoteFlash, setQuoteFlash] = useState<number | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const nearBottomRef = useRef(true);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const commandEnd = composerValue.lastIndexOf("/");
  const showCommands = commandEnd >= 0 && composerValue.slice(commandEnd).trim().length <= 8 && !composerValue.slice(commandEnd).trim().includes(" ") && composerValue.slice(0, commandEnd).trim() === "";
  const mentionMatch = composerValue.match(/@([^\s@]*)$/);
  const mentionOptions = useMemo(() => room?.members.filter((member) => member.nickname.toLowerCase().includes((mentionMatch?.[1] || "").toLowerCase())) || [], [room?.members, mentionMatch?.[1]]);

  const applyMessage = useCallback((message: Message) => {
    setMessages((current) => current.some((item) => item.id === message.id) ? current.map((item) => item.id === message.id ? message : item) : [...current, message]);
  }, []);

  useEffect(() => {
    let canceled = false;
    async function load() {
      setLoading(true);
      setRoomError("");
      const storedIdentity = loadIdentity(sessionId);
      if (!storedIdentity) {
        if (!canceled) setLoading(false);
        return;
      }
      setIdentity(storedIdentity);
      try {
        const [nextRoom, nextMessages, mentions] = await Promise.all([
          api.getRoom(sessionId, storedIdentity.visitor_id),
          api.getMessages(sessionId, storedIdentity.visitor_id),
          api.getMentions(sessionId, storedIdentity.visitor_id),
        ]);
        if (canceled) return;
        setRoom(nextRoom);
        setMessages(nextMessages);
        setUnreadMentionIds(mentions.message_ids);
      } catch (err) {
        if (canceled) return;
        localStorage.removeItem(`roomai:identity:${sessionId}`);
        setIdentity(null);
        setRoomError(err instanceof Error ? err.message : "无法打开这个房间");
      } finally {
        if (!canceled) setLoading(false);
      }
    }
    void load();
    return () => { canceled = true; };
  }, [sessionId]);

  useEffect(() => { nearBottomRef.current = nearBottom; }, [nearBottom]);

  useEffect(() => {
    if (!identity || !room) return;
    const socket = new WebSocket(api.socketUrl(sessionId, identity.visitor_id));
    socketRef.current = socket;
    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "message" || data.type === "message_recalled") {
          applyMessage(data.message as Message);
          if (!nearBottomRef.current) setNewMessages(true);
          const incoming = data.message as Message;
          if (data.type === "message" && incoming.visitor_id !== identity.visitor_id && incoming.content.includes(`@${identity.nickname}`)) {
            setUnreadMentionIds((current) => [...new Set([...current, incoming.id])]);
          }
        }
        if (data.type === "presence") setOnlineIds((data.members as Member[]).map((member) => member.visitor_id));
        if (data.type === "typing" && data.visitor_id !== identity.visitor_id) {
          setTypingNames((current) => data.active ? [...new Set([...current, data.nickname])] : current.filter((name) => name !== data.nickname));
          if (data.active && data.visitor_id !== "roomai") window.setTimeout(() => setTypingNames((current) => current.filter((name) => name !== data.nickname)), 3200);
        }
        if (data.type === "phase_changed") setRoom((current) => current ? { ...current, phase: data.phase } : current);
      } catch {
        // Ignore malformed events and keep the chat connection alive.
      }
    };
    socket.onclose = () => { if (socketRef.current === socket) socketRef.current = null; };
    return () => { socket.close(); socketRef.current = null; };
  }, [applyMessage, identity, room, sessionId]);

  useEffect(() => {
    if (nearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      setNewMessages(false);
    }
  }, [messages, nearBottom]);

  useEffect(() => {
    if (!selectedImage) { setImagePreview(""); return; }
    const url = URL.createObjectURL(selectedImage);
    setImagePreview(url);
    return () => URL.revokeObjectURL(url);
  }, [selectedImage]);

  async function joinByLink(event: FormEvent) {
    event.preventDefault();
    setJoining(true);
    setRoomError("");
    try {
      const joined = await api.joinByLink(sessionId, joinName.trim());
      saveIdentity(joined);
      setIdentity(joined);
      const [nextRoom, nextMessages, mentions] = await Promise.all([api.getRoom(sessionId, joined.visitor_id), api.getMessages(sessionId, joined.visitor_id), api.getMentions(sessionId, joined.visitor_id)]);
      setRoom(nextRoom);
      setMessages(nextMessages);
      setUnreadMentionIds(mentions.message_ids);
    } catch (err) {
      setRoomError(err instanceof Error ? err.message : "无法加入房间");
    } finally {
      setJoining(false);
    }
  }

  function sendTyping(active: boolean) {
    if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify({ type: "typing", active }));
  }

  function handleTyping(value: string) {
    setComposerValue(value);
    if (!identity) return;
    sendTyping(true);
    if (typingTimer.current) clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => sendTyping(false), 2800);
  }

  async function submitMessage(event?: FormEvent) {
    event?.preventDefault();
    if (!identity || sending || (!composerValue.trim() && !selectedImage)) return;
    setSending(true);
    setError("");
    try {
      let image_url: string | undefined;
      if (selectedImage) image_url = (await api.uploadImage(sessionId, identity.visitor_id, selectedImage)).image_url;
      await api.sendMessage(sessionId, {
        visitor_id: identity.visitor_id,
        content: composerValue.trim(),
        reply_to_message_id: replyTo?.id,
        image_url,
      });
      setComposerValue("");
      setReplyTo(null);
      setSelectedImage(null);
      if (composerRef.current) composerRef.current.style.height = "auto";
      setNearBottom(true);
      sendTyping(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败，请重试");
    } finally {
      setSending(false);
    }
  }

  function onComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage();
    }
    if (event.key === "Escape") { setReplyTo(null); setComposerValue(""); }
  }

  async function recall(message: Message) {
    if (!identity || !window.confirm("撤回这条消息？")) return;
    try { applyMessage(await api.recallMessage(sessionId, message.id, identity.visitor_id)); }
    catch (err) { setError(err instanceof Error ? err.message : "撤回失败"); }
  }

  async function togglePhase() {
    if (!room || !identity?.owner_token) return;
    const phase = room.phase === "diverge" ? "converge" : "diverge";
    try {
      await api.setPhase(sessionId, identity, phase);
      setRoom({ ...room, phase });
    } catch (err) { setError(err instanceof Error ? err.message : "切换阶段失败"); }
  }

  async function copyInvite() {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/room/${sessionId}`);
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1800);
    } catch { setError("复制失败，请手动复制浏览器地址"); }
  }

  function jumpToMessage(messageId: number) {
    const target = document.getElementById(`message-${messageId}`);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
    setQuoteFlash(messageId);
    window.setTimeout(() => setQuoteFlash(null), 1500);
    setNearBottom(false);
  }

  async function openUnreadMention() {
    if (!identity || unreadMentionIds.length === 0) return;
    const ids = unreadMentionIds;
    setSearchOpen(false);
    setSearch("");
    setNearBottom(false);
    window.setTimeout(() => jumpToMessage(ids[0]), 80);
    setUnreadMentionIds([]);
    setRoom((current) => current ? { ...current, unread_mentions: 0 } : current);
    try { await api.markMentionsRead(sessionId, identity, ids); }
    catch { setUnreadMentionIds(ids); }
  }

  function onScroll() {
    if (!listRef.current) return;
    const remaining = listRef.current.scrollHeight - listRef.current.scrollTop - listRef.current.clientHeight;
    const atBottom = remaining < 120;
    setNearBottom(atBottom);
    if (atBottom) setNewMessages(false);
  }

  async function selectCommand(command: string) {
    setComposerValue(`${command} `);
    composerRef.current?.focus();
  }

  function selectMention(member: Member) {
    const current = composerValue.replace(/@[^\s@]*$/, `@${member.nickname} `);
    setComposerValue(current);
    composerRef.current?.focus();
  }

  function selectRoomAI() {
    setComposerValue((value) => value.replace(/@[^\s@]*$/, "@RoomAI "));
    composerRef.current?.focus();
  }

  const filteredMessages = useMemo(() => {
    if (!search.trim()) return messages;
    const query = search.trim().toLowerCase();
    return messages.filter((message) => message.content.toLowerCase().includes(query) || message.sender_name.toLowerCase().includes(query));
  }, [messages, search]);
  const onlineMembers = room?.members.filter((member) => onlineIds.includes(member.visitor_id)) || [];

  if (loading) return <div className="room-loading"><span className="brand-mark"><span /><span /><span /></span><span>正在打开讨论室…</span></div>;

  if (!identity || !room) return <main className="join-shell">
    <a className="brand join-brand" href="/"><span className="brand-mark"><span /><span /><span /></span><span>room<span className="brand-ai">ai</span></span></a>
    <form className="join-card" onSubmit={joinByLink}>
      <div className="join-card-icon"><Users size={22} /></div>
      <p className="eyebrow">你收到了一份讨论邀请</p>
      <h1>加入 RoomAI 房间</h1>
      <p className="modal-subtitle">填上昵称，就能看到完整的讨论记录并加入对话。</p>
      <label className="field-label">你的昵称<input autoFocus required maxLength={32} value={joinName} onChange={(event) => setJoinName(event.target.value)} placeholder="伙伴们怎么称呼你？" /></label>
      {roomError && <p className="form-error">{roomError}</p>}
      <button className="button button-primary button-submit" disabled={joining}>{joining ? <><span className="button-spinner" /> 正在加入…</> : <>进入讨论室 <ArrowRight size={17} /></>}</button>
      <a className="join-back" href="/">返回 RoomAI 首页</a>
    </form>
  </main>;

  return <main className="room-app">
    <div className={`sidebar-scrim ${sidebarOpen ? "visible" : ""}`} onClick={() => setSidebarOpen(false)} />
    <aside className={`room-sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
      <div className="sidebar-brand-row"><a className="brand" href="/"><span className="brand-mark"><span /><span /><span /></span><span>room<span className="brand-ai">ai</span></span></a><button className="icon-button mobile-sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="关闭侧栏"><X size={18} /></button></div>
      <div className="sidebar-section-label">讨论空间 <button className="icon-button tiny-icon" onClick={copyInvite} aria-label="复制邀请链接">{linkCopied ? <Check size={14} /> : <Plus size={15} />}</button></div>
      <div className="sidebar-room-current"><span className="room-glyph"><MessageCircle size={16} /></span><span className="sidebar-room-name">{room.topic}</span><ChevronDown size={15} /></div>
      <div className="sidebar-divider" />

      <section className="sidebar-panel summary-panel">
        <div className="panel-heading"><span className="panel-icon panel-icon-violet"><Sparkles size={15} /></span><h3>滚动总结</h3><span className="coming-pill">AI</span></div>
        <p className="panel-empty">讨论开始后，RoomAI 会在这里整理共识、分歧和待办。</p>
        <div className="summary-progress"><span /><span /><span /></div>
      </section>

      <section className="sidebar-panel idea-panel">
        <div className="panel-heading"><span className="panel-icon panel-icon-amber"><Lightbulb size={15} /></span><h3>想法池</h3><span className="count-pill">0</span></div>
        <div className="idea-empty"><div className="idea-empty-mark"><Plus size={15} /></div><span>值得做的想法，会在讨论中浮现</span></div>
      </section>

      <section className="sidebar-panel dimensions-panel">
        <div className="panel-heading"><span className="panel-icon panel-icon-green"><Activity size={15} /></span><h3>讨论维度</h3></div>
        <div className="dimension-list">{["用户", "技术", "时间", "成本", "风险"].map((item) => <div className="dimension-row" key={item}><span className="dimension-dot" />{item}<span>待聊</span></div>)}</div>
      </section>

      <div className="sidebar-spacer" />
      <div className="sidebar-help"><span className="help-avatar"><CircleHelp size={17} /></span><span><b>让讨论更有方向</b><small>试试在消息里 @RoomAI</small></span><ArrowRight size={14} /></div>
      <div className="sidebar-profile"><div className={`avatar avatar-small ${avatarColor(identity.nickname)}`}>{identity.nickname.slice(0, 1)}</div><span><b>{identity.nickname}</b><small>{identity.owner_token ? "房间创建者" : "讨论成员"}</small></span><MoreHorizontal size={18} /></div>
    </aside>

    <section className="room-main">
      <header className="room-topbar">
        <button className="icon-button mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="打开侧栏"><Menu size={20} /></button>
        <div className="room-heading"><span className="room-heading-kicker">当前讨论</span><h1 title={room.topic}>{room.topic}</h1></div>
        <div className="room-top-actions">
          <button className={`phase-button ${room.phase === "converge" ? "phase-converge" : ""}`} onClick={togglePhase} disabled={!identity.owner_token} title={identity.owner_token ? "点击切换讨论阶段" : "发散阶段"}><span />{room.phase === "diverge" ? "发散阶段" : "收敛阶段"}<ChevronDown size={14} /></button>
          <div className="online-group" title={`${onlineMembers.length} 人在线`}><div className="avatar-stack online-stack">{onlineMembers.slice(0, 3).map((member) => <i className={avatarColor(member.nickname)} key={member.visitor_id}>{member.nickname.slice(0, 1)}</i>)}</div><span className="online-count">{onlineMembers.length}<span>在线</span></span></div>
          <span className="topbar-separator" />
          {unreadMentionIds.length > 0 && <button className="mention-notice" onClick={() => void openUnreadMention()} title="跳转到未读 @ 提醒"><AtSign size={15} /><span>{unreadMentionIds.length}</span></button>}
          <button className={`icon-button top-action ${searchOpen ? "active" : ""}`} onClick={() => { setSearchOpen((value) => !value); setSearch(""); }} aria-label="搜索消息"><Search size={18} /></button>
          <button className="icon-button top-action settings-action" onClick={copyInvite} aria-label="复制邀请链接" title="复制邀请链接">{linkCopied ? <ClipboardCheck size={18} /> : <Settings2 size={18} />}</button>
        </div>
      </header>

      {searchOpen && <div className="room-search"><Search size={17} /><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索消息或发言人…" /><span>{search ? `${filteredMessages.length} 条` : "Enter 搜索"}</span><button className="icon-button" onClick={() => { setSearchOpen(false); setSearch(""); }} aria-label="关闭搜索"><X size={16} /></button></div>}

      <section className="room-chat-area">
        <div className="prep-card">
          <div className="prep-leading"><span className="prep-icon"><Clipboard size={16} /></span><span><b>开始前想一想</b><small>围绕主题，先带着这些问题进入讨论</small></span></div>
          <div className="prep-questions">{room.prep_checklist.map((question, index) => <span key={question}><i>0{index + 1}</i>{question}</span>)}</div>
          <button className="prep-collapse" aria-label="收起准备清单"><ChevronDown size={16} /></button>
        </div>

        <div className="message-list" ref={listRef} onScroll={onScroll}>
          <div className="conversation-start"><span className="start-line" /><span>讨论从这里开始</span><span className="start-line" /></div>
          {filteredMessages.map((message, index) => {
            const own = message.visitor_id === identity.visitor_id;
            const previous = filteredMessages[index - 1];
            const showAuthor = !previous || previous.sender_name !== message.sender_name || new Date(message.created_at).getTime() - new Date(previous.created_at).getTime() > 5 * 60 * 1000;
            const mentioned = !own && message.content.includes(`@${identity.nickname}`);
            return <article id={`message-${message.id}`} key={message.id} className={`message-row ${own ? "message-own" : ""} ${message.sender_type === "agent" ? "message-agent-row" : ""} ${mentioned ? "mention-alert" : ""} ${quoteFlash === message.id ? "message-flash" : ""}`}>
              {!own && <div className={`avatar message-avatar ${message.sender_type === "agent" ? "avatar-ai" : avatarColor(message.sender_name)}`}>{message.sender_type === "agent" ? <Sparkles size={15} /> : message.sender_name.slice(0, 1)}</div>}
              <div className="message-stack">
                {showAuthor && <div className={`message-meta ${own ? "meta-own" : ""}`}><span className={message.sender_type === "agent" ? "agent-name" : ""}>{message.sender_type === "agent" && <Sparkles size={12} />}{message.sender_name}{message.sender_type === "agent" && <i className="ai-label">AI 助手</i>}</span><time>{formatTime(message.created_at)}</time></div>}
                <div className={`message-bubble ${own ? "bubble-own" : ""} ${message.sender_type === "agent" ? "bubble-agent" : ""} ${message.is_recalled ? "bubble-recalled" : ""}`}>
                  {message.reply_to && <button className="quoted-message" onClick={() => jumpToMessage(message.reply_to!.id)}><span className="quote-bar" /><span><b>{message.reply_to.sender_name}</b><small>{message.reply_to.content}</small></span></button>}
                  {message.is_recalled ? <span className="recalled-copy">这条消息已撤回</span> : <>
                    {message.content && <p className="message-content">{message.content.split(/(@[\w\u4e00-\u9fff-]+)/g).map((part, partIndex) => part.startsWith("@") ? <mark className="mention-mark" key={partIndex}>{part}</mark> : part)}</p>}
                    {message.image_url && <button className="message-image" onClick={() => window.open(api.imageUrl(message.image_url!), "_blank", "noopener,noreferrer")}><img src={api.imageUrl(message.image_url)} alt="讨论附件" /><span><FileImage size={14} /> 查看图片</span></button>}
                  </>}
                  {!message.is_recalled && <div className="message-tools"><button onClick={() => { setReplyTo(message); composerRef.current?.focus(); }} title="引用回复"><MessageCircle size={13} /> 回复</button>{own && Date.now() - new Date(message.created_at).getTime() <= 120000 && <button onClick={() => void recall(message)} title="两分钟内可以撤回">撤回</button>}</div>}
                </div>
              </div>
              {own && <div className={`avatar message-avatar ${avatarColor(identity.nickname)}`}>{identity.nickname.slice(0, 1)}</div>}
            </article>;
          })}
          {typingNames.length > 0 && <div className="typing-line"><span className="typing-dots"><i /><i /><i /></span>{typingNames.join("、")} 正在输入…</div>}
          <div ref={bottomRef} />
        </div>

        {!nearBottom && newMessages && <button className="new-messages-button" onClick={() => setNearBottom(true)}>有新消息 <ArrowDown size={15} /></button>}
        {error && <div className="message-error"><span>{error}</span><button onClick={() => setError("")} aria-label="关闭"><X size={14} /></button></div>}

        <div className="composer-wrap">
          {replyTo && <div className="reply-context"><span className="quote-bar" /><span><b>回复 {replyTo.sender_name}</b><small>{replyTo.content || "图片"}</small></span><button className="icon-button" onClick={() => setReplyTo(null)} aria-label="取消引用"><X size={15} /></button></div>}
          {imagePreview && <div className="upload-preview"><img src={imagePreview} alt="待发送图片" /><button className="icon-button" onClick={() => setSelectedImage(null)} aria-label="移除图片"><X size={15} /></button></div>}
          <form className="composer" onSubmit={(event) => void submitMessage(event)}>
            <div className="composer-tools"><label className="icon-button attach-button" title="添加图片"><ImagePlus size={18} /><input type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={(event) => { const file = event.target.files?.[0]; if (file && file.size <= 5 * 1024 * 1024) setSelectedImage(file); else if (file) setError("图片大小不能超过 5MB"); event.currentTarget.value = ""; }} /></label><button type="button" className="icon-button command-button" title="快捷指令" onClick={() => { setComposerValue("/"); composerRef.current?.focus(); }}><Command size={17} /></button></div>
            <div className="composer-input-wrap">
              {showCommands && <div className="suggestion-menu command-menu">{commands.filter((command) => command.startsWith(composerValue.slice(commandEnd))).map((command) => <button type="button" onClick={() => void selectCommand(command)} key={command}><span>{command}</span><small>RoomAI 快捷指令</small><ChevronDown size={14} /></button>)}</div>}
              {mentionMatch && mentionOptions.length > 0 && <div className="suggestion-menu mention-menu"><button type="button" onClick={selectRoomAI}><span className="avatar avatar-small avatar-ai"><Sparkles size={13} /></span><b>RoomAI</b><small>AI 助手</small></button>{mentionOptions.map((member) => <button type="button" onClick={() => selectMention(member)} key={member.visitor_id}><span className={`avatar avatar-small ${avatarColor(member.nickname)}`}>{member.nickname.slice(0, 1)}</span><b>{member.nickname}</b>{member.is_owner ? <small>房主</small> : null}</button>)}</div>}
              <textarea ref={composerRef} rows={1} value={composerValue} onChange={(event) => { handleTyping(event.target.value); event.currentTarget.style.height = "auto"; event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 140)}px`; }} onKeyDown={onComposerKey} placeholder="说说你的想法…（输入 / 查看指令，@ 提及伙伴）" />
            </div>
            <button className="send-button" disabled={sending || (!composerValue.trim() && !selectedImage)} aria-label="发送消息">{sending ? <LoaderCircle size={18} className="spin" /> : <Send size={17} />}</button>
          </form>
          <div className="composer-foot"><span><Paperclip size={12} /> 图片最大 5MB</span><span><kbd>Enter</kbd> 发送 <i /> <kbd>Shift + Enter</kbd> 换行</span></div>
        </div>
      </section>
    </section>
  </main>;
}

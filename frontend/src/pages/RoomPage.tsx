import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ArrowDown, ArrowRight, AtSign, Check, ChevronDown, Clipboard, ClipboardCheck, Clock3, Command, Copy, History, ImagePlus, LoaderCircle, Menu, Reply, Search, Send, Settings2, Sparkles, Undo2, X } from "lucide-react";
import { api, Identity, Idea, loadIdentity, Member, Message, Room, RoomUsage, saveIdentity, SummaryVersion, Todo } from "../lib/api";
import MarkdownContent from "../components/MarkdownContent";
import VoteCard, { Vote } from "../components/VoteCard";

const commands = ["/总结", "/评估", "/投票", "/画图", "/挑战", "/待办", "/结束", "/帮助"];

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export default function RoomPage() {
  const { sessionId = "" } = useParams();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [summary, setSummary] = useState<SummaryVersion | null>(null);
  const [summaryVersions, setSummaryVersions] = useState<SummaryVersion[]>([]);
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [usage, setUsage] = useState<RoomUsage | null>(null);
  const [votes, setVotes] = useState<Vote[]>([]);
  const [unreadMentionIds, setUnreadMentionIds] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [roomError, setRoomError] = useState("");
  const [onlineIds, setOnlineIds] = useState<string[]>([]);
  const [typingNames, setTypingNames] = useState<string[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [newMessages, setNewMessages] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [summaryHistoryOpen, setSummaryHistoryOpen] = useState(false);
  const [editingTodo, setEditingTodo] = useState<Todo | null>(null);
  const [todoSaving, setTodoSaving] = useState(false);
  const [voteBusyIds, setVoteBusyIds] = useState<number[]>([]);
  const [modelSaving, setModelSaving] = useState(false);
  const [prepOpen, setPrepOpen] = useState(true);
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
  const [menu, setMenu] = useState<{ message: Message; x: number; y: number } | null>(null);
  const [confirmRecall, setConfirmRecall] = useState(false);
  const [pressingId, setPressingId] = useState<number | null>(null);
  const [toast, setToast] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const nearBottomRef = useRef(true);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pressStart = useRef<{ x: number; y: number } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
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
        const [nextRoom, nextMessages, mentions, nextSummary, nextIdeas, nextUsage, nextVotes, nextTodos] = await Promise.all([
          api.getRoom(sessionId, storedIdentity.visitor_id),
          api.getMessages(sessionId, storedIdentity.visitor_id),
          api.getMentions(sessionId, storedIdentity.visitor_id),
          api.getSummary(sessionId, storedIdentity.visitor_id),
          api.getIdeas(sessionId, storedIdentity.visitor_id),
          api.getUsage(sessionId, storedIdentity.visitor_id),
          api.getVotes(sessionId, storedIdentity.visitor_id),
          api.getTodos(sessionId, storedIdentity.visitor_id),
        ]);
        if (canceled) return;
        setRoom(nextRoom);
        setMessages(nextMessages);
        setSummary(nextSummary.latest);
        setSummaryVersions(nextSummary.history);
        setIdeas(nextIdeas.ideas);
        setUsage(nextUsage);
        setVotes(nextVotes.votes);
        setTodos(nextTodos.todos);
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
    if (loading || !window.location.hash.startsWith("#message-")) return;
    const messageId = Number(window.location.hash.slice("#message-".length));
    if (!Number.isInteger(messageId)) return;
    const timer = window.setTimeout(() => {
      document.getElementById(`message-${messageId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
      setQuoteFlash(messageId);
      setNearBottom(false);
      window.setTimeout(() => setQuoteFlash(null), 1500);
    }, 120);
    return () => window.clearTimeout(timer);
  }, [loading, sessionId]);

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
          if (data.type === "message" && incoming.sender_type === "agent") {
            void api.getUsage(sessionId, identity.visitor_id).then(setUsage).catch(() => undefined);
          }
        }
        if (data.type === "presence") setOnlineIds((data.members as Member[]).map((member) => member.visitor_id));
        if (data.type === "typing" && data.visitor_id !== identity.visitor_id) {
          setTypingNames((current) => data.active ? [...new Set([...current, data.nickname])] : current.filter((name) => name !== data.nickname));
          if (data.active && data.visitor_id !== "roomai") window.setTimeout(() => setTypingNames((current) => current.filter((name) => name !== data.nickname)), 3200);
        }
        if (data.type === "phase_changed") setRoom((current) => current ? { ...current, phase: data.phase } : current);
        if (data.type === "model_changed") setRoom((current) => current ? { ...current, model: data.model } : current);
        if (data.type === "summary_update") {
          const nextSummary = {
            version: data.summary.version,
            content: data.summary,
            through_message_id: data.summary.through_message_id,
            created_at: data.summary.created_at,
          } as SummaryVersion;
          setSummary(nextSummary);
          setSummaryVersions((current) => [nextSummary, ...current.filter((item) => item.version !== nextSummary.version)].slice(0, 20));
          void api.getUsage(sessionId, identity.visitor_id).then(setUsage).catch(() => undefined);
        }
        if (data.type === "idea_update") setIdeas(data.ideas as Idea[]);
        if (data.type === "vote_created") setVotes((current) => [...current.filter((vote) => vote.id !== data.vote.id), data.vote as Vote]);
        if (data.type === "vote_update") setVotes((current) => current.map((vote) => vote.id === data.vote.id ? { ...vote, ...(data.vote as Vote), my_option_id: vote.my_option_id } : vote));
        if (data.type === "todo_update") setTodos(data.todos as Todo[]);
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

  useEffect(() => {
    if (!menu) return;
    function onKey(event: globalThis.KeyboardEvent) { if (event.key === "Escape") closeMenu(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  async function joinByLink(event: FormEvent) {
    event.preventDefault();
    setJoining(true);
    setRoomError("");
    try {
      const joined = await api.joinByLink(sessionId, joinName.trim());
      saveIdentity(joined);
      setIdentity(joined);
      const [nextRoom, nextMessages, mentions, nextSummary, nextIdeas, nextUsage, nextVotes, nextTodos] = await Promise.all([
        api.getRoom(sessionId, joined.visitor_id), api.getMessages(sessionId, joined.visitor_id), api.getMentions(sessionId, joined.visitor_id),
        api.getSummary(sessionId, joined.visitor_id), api.getIdeas(sessionId, joined.visitor_id), api.getUsage(sessionId, joined.visitor_id),
        api.getVotes(sessionId, joined.visitor_id),
        api.getTodos(sessionId, joined.visitor_id),
      ]);
      setRoom(nextRoom);
      setMessages(nextMessages);
      setSummary(nextSummary.latest);
      setSummaryVersions(nextSummary.history);
      setIdeas(nextIdeas.ideas);
      setUsage(nextUsage);
      setVotes(nextVotes.votes);
      setTodos(nextTodos.todos);
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

  function showToast(text: string) {
    setToast(text);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 1800);
  }

  function openMenu(message: Message, clientX: number, clientY: number) {
    // 桌面端浮窗：贴着按压点打开，别超出视口；手机上 CSS 会把它变成底部卡片。
    const width = 200;
    const height = 180;
    const x = Math.min(Math.max(8, clientX), window.innerWidth - width - 8);
    const y = clientY + height > window.innerHeight ? Math.max(8, clientY - height) : clientY;
    setConfirmRecall(false);
    setMenu({ message, x, y });
  }

  function closeMenu() {
    setMenu(null);
    setConfirmRecall(false);
  }

  function startPress(message: Message, event: React.PointerEvent) {
    if (message.is_recalled) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    pressStart.current = { x: event.clientX, y: event.clientY };
    setPressingId(message.id);
    pressTimer.current = setTimeout(() => {
      if (navigator.vibrate) navigator.vibrate(10);
      setPressingId(null);
      openMenu(message, event.clientX, event.clientY);
    }, 500);
  }

  function cancelPress() {
    if (pressTimer.current) clearTimeout(pressTimer.current);
    pressTimer.current = null;
    pressStart.current = null;
    setPressingId(null);
  }

  function handlePressMove(event: React.PointerEvent) {
    // 手指滑动超过 10px 说明是在滚动列表，不是长按。
    if (!pressStart.current) return;
    if (Math.abs(event.clientX - pressStart.current.x) > 10 || Math.abs(event.clientY - pressStart.current.y) > 10) cancelPress();
  }

  function canRecall(message: Message) {
    return !!identity && message.visitor_id === identity.visitor_id && Date.now() - new Date(message.created_at).getTime() <= 120000;
  }

  function replyFromMenu(message: Message) {
    setReplyTo(message);
    composerRef.current?.focus();
    closeMenu();
  }

  async function copyMessage(message: Message) {
    try {
      await navigator.clipboard.writeText(message.content || "图片");
      showToast("已复制");
    } catch {
      setError("复制失败，请手动长按复制");
    }
    closeMenu();
  }

  async function recall(message: Message) {
    if (!identity) return;
    closeMenu();
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

  async function changeModel(model: string) {
    if (!identity || !room || !identity.owner_token || modelSaving) return;
    setModelSaving(true);
    try {
      await api.setModel(sessionId, identity, model);
      setRoom((current) => current ? { ...current, model } : current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "切换模型失败");
    } finally {
      setModelSaving(false);
    }
  }

  async function castVote(voteId: number, optionId: number) {
    if (!identity || voteBusyIds.includes(voteId)) return;
    setVoteBusyIds((current) => [...current, voteId]);
    try {
      const result = await api.castVote(sessionId, voteId, identity.visitor_id, optionId);
      setVotes((current) => current.map((vote) => vote.id === voteId ? result.vote : vote));
    } catch (err) {
      setError(err instanceof Error ? err.message : "投票失败，请重试");
    } finally {
      setVoteBusyIds((current) => current.filter((id) => id !== voteId));
    }
  }

  async function closeVote(voteId: number) {
    if (!identity || !identity.owner_token || voteBusyIds.includes(voteId)) return;
    setVoteBusyIds((current) => [...current, voteId]);
    try {
      const result = await api.closeVote(sessionId, voteId, identity);
      setVotes((current) => current.map((vote) => vote.id === voteId ? result.vote : vote));
    } catch (err) {
      setError(err instanceof Error ? err.message : "结束投票失败");
    } finally {
      setVoteBusyIds((current) => current.filter((id) => id !== voteId));
    }
  }

  async function saveTodo(event: FormEvent) {
    event.preventDefault();
    if (!identity || !editingTodo || todoSaving) return;
    setTodoSaving(true);
    try {
      const result = await api.updateTodo(sessionId, identity.visitor_id, {
        id: editingTodo.id,
        title: editingTodo.title.trim(),
        assignee: editingTodo.assignee.trim(),
        status: editingTodo.status,
        due_at: editingTodo.due_at,
      });
      setTodos((current) => current.map((todo) => todo.id === result.todo.id ? result.todo : todo));
      setEditingTodo(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存待办失败");
    } finally {
      setTodoSaving(false);
    }
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
    if (menu) closeMenu();
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
  const votesByMessage = useMemo(() => new Map(votes.map((vote) => [vote.message_id, vote])), [votes]);
  const onlineMembers = room?.members.filter((member) => onlineIds.includes(member.visitor_id)) || [];

  if (loading) return <div className="room-loading"><span className="brand-mark"><span /><span /><span /></span><span>正在打开讨论室…</span></div>;

  if (!identity || !room) return <main className="join-shell">
    <a className="brand join-brand" href="/"><span className="brand-mark"><span /><span /><span /></span><span>roomai</span></a>
    <form className="join-card" onSubmit={joinByLink}>
      <p className="eyebrow">你收到了一份讨论邀请</p>
      <h1>加入 RoomAI 房间</h1>
      <p className="modal-sub">填上昵称，就能看到完整的讨论记录并加入对话。</p>
      <label className="field">你的昵称<input autoFocus required maxLength={32} value={joinName} onChange={(event) => setJoinName(event.target.value)} placeholder="伙伴们怎么称呼你？" /></label>
      {roomError && <p className="form-error">{roomError}</p>}
      <button className="btn btn-primary btn-block" disabled={joining}>{joining ? <><span className="btn-spinner" /> 正在加入…</> : <>进入讨论室 <ArrowRight size={16} /></>}</button>
      <a className="join-back" href="/">返回 RoomAI 首页</a>
    </form>
  </main>;

  return <main className="room">
    <div className={`side-scrim ${sidebarOpen ? "visible" : ""}`} onClick={() => setSidebarOpen(false)} />
    <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
      <div className="side-top">
        <a className="brand" href="/"><span className="brand-mark"><span /><span /><span /></span><span>roomai</span></a>
        <button className="icon-btn mobile-side-close" onClick={() => setSidebarOpen(false)} aria-label="关闭侧栏"><X size={17} /></button>
      </div>

      <div className="side-room">
        <span className="side-room-name">{room.topic}</span>
        <button className="icon-btn" onClick={copyInvite} aria-label="复制邀请链接" title="复制邀请链接">{linkCopied ? <Check size={15} /> : <Clipboard size={15} />}</button>
      </div>

      <div className="side-label-row">
        <p className="side-label">滚动总结</p>
        <button className="history-button" disabled={!summaryVersions.length} onClick={() => setSummaryHistoryOpen(true)}><History size={13} />历史</button>
      </div>
      {summary ? <>
        <p className="summary-ver">最近更新 · v{summary.version}</p>
        <ul className="summary-list">{(summary.content.consensus.length ? summary.content.consensus : ["暂时还没有形成明确共识。"]).slice(0, 3).map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
        {summary.content.disagreements.length > 0 && <div className="summary-sub"><b>待讨论</b><span>{summary.content.disagreements[0]}</span></div>}
        {summary.content.todos.length > 0 && <div className="summary-sub"><b>下一步</b><span>{summary.content.todos[0].title}{summary.content.todos[0].assignee ? ` · ${summary.content.todos[0].assignee}` : ""}</span></div>}
      </> : <p className="side-empty">讨论开始后，RoomAI 会在这里整理共识、分歧和待办。</p>}

      <div className="side-label-row todo-heading"><p className="side-label">待办</p><span className="count">{todos.filter((todo) => todo.status === "open").length} 未完成</span></div>
      {todos.length ? <div className="todo-list">{todos.slice(0, 6).map((todo) => <button className={`todo-item ${todo.status !== "open" ? "todo-item-closed" : ""}`} key={todo.id} onClick={() => setEditingTodo(todo)}>
        <span className="todo-state-mark">{todo.status === "done" ? "✓" : todo.status === "dropped" ? "–" : "○"}</span>
        <span className="todo-item-copy"><b>{todo.title}</b><small>{todo.assignee || "待分配"} · {todo.status === "open" ? "未完成" : todo.status === "done" ? "已完成" : "已取消"}{todo.due_at ? ` · ${todo.due_at} 截止` : ""}</small></span>
      </button>)}</div> : <p className="side-empty">讨论中明确的下一步会整理到这里。</p>}

      <div className="side-label-row"><p className="side-label">想法池</p><span className="count">{ideas.length}</span></div>
      {ideas.length ? <div className="idea-list">{ideas.slice(0, 5).map((idea) => <button className="idea-item" key={idea.id} onClick={() => idea.source_message_id && jumpToMessage(idea.source_message_id)}>
        <span className="idea-item-title">{idea.title}</span><span className="idea-item-meta">{idea.average_score !== null ? `${idea.average_score.toFixed(1)} / 5` : "待评估"}{idea.proposer_name ? ` · ${idea.proposer_name}` : ""}</span>
      </button>)}</div> : <p className="side-empty">值得做的想法，会在讨论中浮现。</p>}

      <p className="side-label">讨论维度</p>
      <div className="dim-list">{[["用户", "user"], ["技术", "technology"], ["时间", "time"], ["成本", "cost"], ["风险", "risk"]].map(([label, key]) => {
        const covered = Boolean(summary?.content.dimension_coverage[key]);
        return <div className={`dim-row ${covered ? "dim-done" : ""}`} key={key}><span className="dim-dot" />{label}<span>{covered ? "已讨论" : "待聊"}</span></div>;
      })}</div>

      <a className="sidebar-replay-link" href={`/room/${sessionId}/replay`}><Clock3 size={15} /><span>讨论回放</span><ArrowRight size={13} /></a>
      <div className="side-spacer" />
      <div className="side-me">
        <div className="avatar avatar-sm">{identity.nickname.slice(0, 1)}</div>
        <span><b>{identity.nickname}</b><small>{identity.owner_token ? "房间创建者" : "讨论成员"}</small></span>
      </div>
    </aside>

    <section className="main">
      <header className="topbar">
        <button className="icon-btn mobile-menu" onClick={() => setSidebarOpen(true)} aria-label="打开侧栏"><Menu size={19} /></button>
        <div className="topbar-title"><h1 title={room.topic}>{room.topic}</h1></div>
        <button className={`phase-btn ${room.phase === "converge" ? "phase-converge" : ""}`} onClick={togglePhase} disabled={!identity.owner_token} title={identity.owner_token ? "点击切换讨论阶段" : "发散阶段"}><i />{room.phase === "diverge" ? "发散" : "收敛"}</button>
        <div className="online" title={`${onlineMembers.length} 人在线`}><span className="online-stack">{onlineMembers.slice(0, 3).map((member) => <i key={member.visitor_id}>{member.nickname.slice(0, 1)}</i>)}</span><span>{onlineMembers.length} 在线</span></div>
        <span className="top-sep" />
        {unreadMentionIds.length > 0 && <button className="mention-badge" onClick={() => void openUnreadMention()} title="跳转到未读 @ 提醒"><AtSign size={13} /><span>{unreadMentionIds.length}</span></button>}
        <button className={`icon-btn ${searchOpen ? "active" : ""}`} onClick={() => { setSearchOpen((value) => !value); setSearch(""); }} aria-label="搜索消息"><Search size={17} /></button>
        <button className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="房间设置" title="房间设置"><Settings2 size={17} /></button>
        <button className="icon-btn" onClick={copyInvite} aria-label="复制邀请链接" title="复制邀请链接">{linkCopied ? <ClipboardCheck size={17} /> : <Clipboard size={17} />}</button>
      </header>

      {searchOpen && <div className="searchbar"><Search size={16} /><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索消息或发言人…" /><span>{search ? `${filteredMessages.length} 条` : "输入关键词筛选"}</span><button className="icon-btn" onClick={() => { setSearchOpen(false); setSearch(""); }} aria-label="关闭搜索"><X size={15} /></button></div>}

      <section className="chat">
        {room.prep_checklist.length > 0 && <div className="prep">
          <button className="prep-head" onClick={() => setPrepOpen((value) => !value)}><ChevronDown size={15} className={prepOpen ? "open" : ""} />开始前想一想<span>{room.prep_checklist.length} 个问题</span></button>
          {prepOpen && <ol className="prep-body">{room.prep_checklist.map((question) => <li key={question}>{question}</li>)}</ol>}
        </div>}

        <div className="msg-list" ref={listRef} onScroll={onScroll}>
          <div className="msg-start">讨论从这里开始</div>
          {filteredMessages.map((message, index) => {
            const own = message.visitor_id === identity.visitor_id;
            const previous = filteredMessages[index - 1];
            const showAuthor = !previous || previous.sender_name !== message.sender_name || new Date(message.created_at).getTime() - new Date(previous.created_at).getTime() > 5 * 60 * 1000;
            const mentioned = !own && message.content.includes(`@${identity.nickname}`);
            return <article id={`message-${message.id}`} key={message.id} className={`msg ${own ? "msg-own" : ""} ${mentioned ? "msg-mention" : ""} ${quoteFlash === message.id ? "msg-flash" : ""} ${menu?.message.id === message.id ? "msg-open" : ""}`}>
              <div className={`avatar msg-avatar ${message.sender_type === "agent" ? "avatar-ai" : ""}`}>{message.sender_type === "agent" ? <Sparkles size={13} /> : message.sender_name.slice(0, 1)}</div>
              <div className="msg-main">
                {showAuthor && <div className="msg-meta"><b className={message.sender_type === "agent" ? "agent-name" : ""}>{message.sender_name}</b>{message.sender_type === "agent" && <i className="tag-ai">AI</i>}<time>{formatTime(message.created_at)}</time></div>}
                <div className={`msg-body ${pressingId === message.id ? "msg-pressing" : ""}`} onPointerDown={(event) => startPress(message, event)} onPointerUp={cancelPress} onPointerLeave={cancelPress} onPointerMove={handlePressMove} onContextMenu={(event) => { event.preventDefault(); if (!message.is_recalled) openMenu(message, event.clientX, event.clientY); }}>
                  {message.reply_to && <button className="quote" onClick={() => jumpToMessage(message.reply_to!.id)}><span><b>{message.reply_to.sender_name}</b><small>{message.reply_to.content}</small></span></button>}
                  {message.is_recalled ? <span className="msg-recalled">这条消息已撤回</span> : <>
                    {message.content && (message.sender_type === "agent"
                      ? <MarkdownContent content={message.content} />
                      : <p className="msg-text">{message.content.split(/(@[\w\u4e00-\u9fff-]+)/g).map((part, partIndex) => part.startsWith("@") ? <mark className="mention-mark" key={partIndex}>{part}</mark> : part)}</p>)}
                    {message.image_url && <button className="msg-img" onClick={() => window.open(api.imageUrl(message.image_url!), "_blank", "noopener,noreferrer")}><img src={api.imageUrl(message.image_url)} alt="讨论附件" /></button>}
                  </>}
                </div>
                {votesByMessage.has(message.id) && <VoteCard vote={votesByMessage.get(message.id)!} isOwner={Boolean(identity.owner_token)} busy={voteBusyIds.includes(votesByMessage.get(message.id)!.id)} onVote={(voteId, optionId) => void castVote(voteId, optionId)} onClose={(voteId) => void closeVote(voteId)} />}
              </div>
            </article>;
          })}
          {typingNames.length > 0 && <div className="typing"><span className="typing-dots"><i /><i /><i /></span>{typingNames.join("、")} 正在输入…</div>}
          <div ref={bottomRef} />
        </div>

        {!nearBottom && newMessages && <button className="new-msg-btn" onClick={() => setNearBottom(true)}>有新消息 <ArrowDown size={14} /></button>}
        {error && <div className="alert"><span>{error}</span><button onClick={() => setError("")} aria-label="关闭"><X size={14} /></button></div>}
        {toast && <div className="toast"><Check size={13} /><span>{toast}</span></div>}
        {menu && <>
          <div className="menu-backdrop" onClick={closeMenu} onContextMenu={(event) => event.preventDefault()} />
          <div className="menu" style={{ left: menu.x, top: menu.y }} role="menu">
            {confirmRecall ? <>
              <div className="menu-title">撤回这条消息？</div>
              <div className="menu-sub">撤回后，所有人都会看到“这条消息已撤回”。</div>
              <button className="menu-danger" onClick={() => void recall(menu.message)}><Undo2 size={15} /> 确定撤回</button>
              <button onClick={() => setConfirmRecall(false)}><X size={15} /> 取消</button>
            </> : <>
              <button onClick={() => replyFromMenu(menu.message)}><Reply size={15} /> 引用回复</button>
              {menu.message.content && <button onClick={() => void copyMessage(menu.message)}><Copy size={15} /> 复制</button>}
              {canRecall(menu.message) && <>
                <div className="menu-sep" />
                <button className="menu-danger" onClick={() => setConfirmRecall(true)}><Undo2 size={15} /> 撤回</button>
              </>}
            </>}
          </div>
        </>}

        <div className="composer-wrap">
          {replyTo && <div className="reply-bar"><span><b>回复 {replyTo.sender_name}</b><small>{replyTo.content || "图片"}</small></span><button className="icon-btn" onClick={() => setReplyTo(null)} aria-label="取消引用"><X size={14} /></button></div>}
          {imagePreview && <div className="upload-preview"><img src={imagePreview} alt="待发送图片" /><button className="icon-btn" onClick={() => setSelectedImage(null)} aria-label="移除图片"><X size={13} /></button></div>}
          <form className="composer" onSubmit={(event) => void submitMessage(event)}>
            <div className="composer-tools">
              <label className="icon-btn attach" title="添加图片"><ImagePlus size={17} /><input type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={(event) => { const file = event.target.files?.[0]; if (file && file.size <= 5 * 1024 * 1024) setSelectedImage(file); else if (file) setError("图片大小不能超过 5MB"); event.currentTarget.value = ""; }} /></label>
              <button type="button" className="icon-btn" title="快捷指令" onClick={() => { setComposerValue("/"); composerRef.current?.focus(); }}><Command size={16} /></button>
            </div>
            <div className="composer-input">
              {showCommands && <div className="suggest suggest-cmd">{commands.filter((command) => command.startsWith(composerValue.slice(commandEnd))).map((command) => <button type="button" onClick={() => void selectCommand(command)} key={command}><span>{command}</span><small>RoomAI 快捷指令</small></button>)}</div>}
              {mentionMatch && mentionOptions.length > 0 && <div className="suggest suggest-mention"><button type="button" onClick={selectRoomAI}><span className="avatar avatar-sm avatar-ai"><Sparkles size={12} /></span><b>RoomAI</b><small>AI 助手</small></button>{mentionOptions.map((member) => <button type="button" onClick={() => selectMention(member)} key={member.visitor_id}><span className="avatar avatar-sm">{member.nickname.slice(0, 1)}</span><b>{member.nickname}</b>{member.is_owner ? <small>房主</small> : null}</button>)}</div>}
              <textarea ref={composerRef} rows={1} maxLength={2000} value={composerValue} onChange={(event) => { handleTyping(event.target.value); event.currentTarget.style.height = "auto"; event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 140)}px`; }} onKeyDown={onComposerKey} placeholder="说说你的想法…（输入 / 查看指令，@ 提及伙伴）" />
            </div>
            <button className="send-btn" disabled={sending || (!composerValue.trim() && !selectedImage)} aria-label="发送消息">{sending ? <LoaderCircle size={17} className="spin" /> : <Send size={16} />}</button>
          </form>
          <div className="composer-hint"><span>图片最大 5MB</span><span><kbd>Enter</kbd> 发送 · <kbd>Shift + Enter</kbd> 换行</span></div>
        </div>
      </section>
    </section>
    {settingsOpen && <div className="overlay" onClick={() => setSettingsOpen(false)}>
      <section className="modal settings" role="dialog" aria-modal="true" aria-labelledby="settings-title" onClick={(event) => event.stopPropagation()}>
        <button className="icon-btn modal-close" onClick={() => setSettingsOpen(false)} aria-label="关闭设置"><X size={17} /></button>
        <p className="eyebrow">房间设置</p>
        <h2 id="settings-title">设置</h2>
        <div className="settings-block">
          <label className="field" htmlFor="model-select">讨论模型</label>
          <select id="model-select" value={room.model} disabled={!identity.owner_token || modelSaving || room.available_models.length < 2} onChange={(event) => void changeModel(event.target.value)}>
            {room.available_models.map((model) => <option key={model} value={model}>{model}</option>)}
          </select>
          <p className="field-note">{identity.owner_token ? "切换后仅影响之后的新调用。" : "只有房主可以切换模型。"}</p>
        </div>
        <div className="settings-usage"><b>本次讨论用量</b><span>{(usage?.total_tokens || 0).toLocaleString("zh-CN")} tokens</span><small>{usage?.cost_configured ? `估算费用 ¥${usage.cost_estimate.toFixed(4)}` : "费用价格未配置；可在 .env 设置模型单价。"}</small></div>
        <div className="settings-block">
          <button className="btn btn-block" onClick={() => void copyInvite()}>{linkCopied ? <ClipboardCheck size={15} /> : <Clipboard size={15} />} {linkCopied ? "已复制邀请链接" : "复制邀请链接"}</button>
          <p className="field-note">房主凭证保存在当前浏览器，清除浏览器数据后将无法管理房间。</p>
        </div>
      </section>
    </div>}
    {editingTodo && <div className="overlay" onClick={() => setEditingTodo(null)}>
      <form className="modal todo-modal" onSubmit={(event) => void saveTodo(event)} onClick={(event) => event.stopPropagation()}>
        <button type="button" className="icon-btn modal-close" onClick={() => setEditingTodo(null)} aria-label="关闭待办编辑"><X size={17} /></button>
        <p className="eyebrow">下一步</p>
        <h2>更新待办</h2>
        <label className="field">待办内容<input required maxLength={300} value={editingTodo.title} onChange={(event) => setEditingTodo((current) => current ? { ...current, title: event.target.value } : current)} /></label>
        <label className="field">负责人<input maxLength={80} value={editingTodo.assignee} onChange={(event) => setEditingTodo((current) => current ? { ...current, assignee: event.target.value } : current)} placeholder="待分配" /></label>
        <label className="field">截止日期<input type="date" value={editingTodo.due_at || ""} onChange={(event) => setEditingTodo((current) => current ? { ...current, due_at: event.target.value || null } : current)} /></label>
        <label className="field">状态<select value={editingTodo.status} onChange={(event) => setEditingTodo((current) => current ? { ...current, status: event.target.value as Todo["status"] } : current)}><option value="open">未完成</option><option value="done">已完成</option><option value="dropped">已取消</option></select></label>
        <button className="btn btn-primary btn-block" disabled={todoSaving}>{todoSaving ? "正在保存…" : "保存待办"}</button>
      </form>
    </div>}
    {summaryHistoryOpen && <div className="overlay" onClick={() => setSummaryHistoryOpen(false)}>
      <section className="modal summary-history-dialog" role="dialog" aria-modal="true" aria-labelledby="summary-history-title" onClick={(event) => event.stopPropagation()}>
        <div className="summary-history-heading"><div><span className="eyebrow">ROOMAI MEMORY</span><h2 id="summary-history-title">总结历史</h2></div><button className="icon-btn" onClick={() => setSummaryHistoryOpen(false)} aria-label="关闭历史总结"><X size={17} /></button></div>
        <div className="summary-history-list">{summaryVersions.map((version) => <article className="summary-history-item" key={version.version}>
          <header><b>版本 {version.version}</b><time>{formatTime(version.created_at)}</time></header>
          <h3>共识</h3>{version.content.consensus.length ? version.content.consensus.map((item, index) => <p key={`${index}-${item}`}>{item}</p>) : <p>暂无明确共识</p>}
          {version.content.disagreements.length > 0 && <><h3>分歧</h3>{version.content.disagreements.map((item, index) => <p key={`${index}-${item}`}>{item}</p>)}</>}
          {version.content.todos.length > 0 && <><h3>待办</h3>{version.content.todos.map((item, index) => <p key={`${index}-${item.title}`}>{item.title}{item.assignee ? ` · ${item.assignee}` : ""}</p>)}</>}
        </article>)}</div>
      </section>
    </div>}
  </main>;
}

import { Fragment, FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowDown, ArrowRight, AtSign, Check, ChevronDown, Clipboard, ClipboardCheck, Clock3, Command, Copy, EyeOff, FilePlus2, FileText, History, ImagePlus, LoaderCircle, Menu, Mic, Reply, Search, Send, Settings2, Sparkles, ThumbsDown, ThumbsUp, Trash2, Undo2, X } from "lucide-react";
import { api, FinishReview, Identity, Idea, IdeaStatus, loadIdentity, Member, Message, Room, RoomDocument, RoomSearchResult, RoomUsage, saveIdentity, SummaryVersion, Todo } from "../lib/api";
import MarkdownContent from "../components/MarkdownContent";
import TodoCard from "../components/TodoCard";
import VoteCard, { Vote } from "../components/VoteCard";

const commands = ["/总结", "/评估", "/联网评估", "/投票", "/对比", "/画图", "/挑战", "/用户视角", "/待办", "/结束", "/帮助"];
const dimensionLabels = [["用户", "user"], ["技术", "technology"], ["时间", "time"], ["成本", "cost"], ["风险", "risk"]] as const;
const atmosphereLabels = { active: "讨论活跃", steady: "节奏平稳", quiet: "讨论偏冷", tense: "存在明显分歧" } as const;
const ideaStatusLabels: Record<IdeaStatus, string> = { considering: "讨论中", pending: "待定", adopted: "已采纳", rejected: "已否决" };
const emptyUsage: RoomUsage = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost_estimate: 0, cost_configured: false };

type SpeechAlternative = { transcript: string };
type SpeechResult = ArrayLike<SpeechAlternative> & { isFinal: boolean };
type SpeechResultEvent = { results: ArrayLike<SpeechResult> };
type SpeechErrorEvent = { error?: string };
type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechResultEvent) => void) | null;
  onerror: ((event: SpeechErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};
type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

function getSpeechRecognitionConstructor() {
  const browser = window as Window & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return browser.SpeechRecognition || browser.webkitSpeechRecognition;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function localDayKey(value: string | Date) {
  const date = value instanceof Date ? value : new Date(value);
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

function formatDayDivider(value: string) {
  const date = new Date(value);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (localDayKey(date) === localDayKey(today)) return "今天";
  if (localDayKey(date) === localDayKey(yesterday)) return "昨天";
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" }).format(date);
}

function formatFileSize(value: number) {
  return value >= 1024 * 1024 ? `${(value / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.ceil(value / 1024))} KB`;
}

export default function RoomPage() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [summary, setSummary] = useState<SummaryVersion | null>(null);
  const [summaryVersions, setSummaryVersions] = useState<SummaryVersion[]>([]);
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [ideaVoteBusyIds, setIdeaVoteBusyIds] = useState<number[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [documents, setDocuments] = useState<RoomDocument[]>([]);
  const [documentUploading, setDocumentUploading] = useState(false);
  const [usage, setUsage] = useState<RoomUsage | null>(null);
  const [votes, setVotes] = useState<Vote[]>([]);
  const [unreadMentionIds, setUnreadMentionIds] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [roomError, setRoomError] = useState("");
  const [onlineIds, setOnlineIds] = useState<string[]>([]);
  const [typingNames, setTypingNames] = useState<string[]>([]);
  const [streamingMessages, setStreamingMessages] = useState<{ stream_id: string; content: string; created_at: string }[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [newMessages, setNewMessages] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<RoomSearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [roomDeleteOpen, setRoomDeleteOpen] = useState(false);
  const [roomDeleting, setRoomDeleting] = useState(false);
  const [roomDeleteError, setRoomDeleteError] = useState("");
  const [finishReviewOpen, setFinishReviewOpen] = useState(false);
  const [finishReview, setFinishReview] = useState<FinishReview | null>(null);
  const [finishReviewLoading, setFinishReviewLoading] = useState(false);
  const [finishSaving, setFinishSaving] = useState(false);
  const [summaryHistoryOpen, setSummaryHistoryOpen] = useState(false);
  const [editingTodo, setEditingTodo] = useState<Todo | null>(null);
  const [todoSaving, setTodoSaving] = useState(false);
  const [todoBusyIds, setTodoBusyIds] = useState<number[]>([]);
  const [voteBusyIds, setVoteBusyIds] = useState<number[]>([]);
  const [modelSaving, setModelSaving] = useState(false);
  const [prepOpen, setPrepOpen] = useState(true);
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [composerValue, setComposerValue] = useState("");
  const [anonymousSubmission, setAnonymousSubmission] = useState(false);
  const [draftHydrated, setDraftHydrated] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [voiceListening, setVoiceListening] = useState(false);
  const [socketStatus, setSocketStatus] = useState<"connecting" | "connected" | "reconnecting" | "disconnected">("connecting");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [linkCopied, setLinkCopied] = useState(false);
  const [joinName, setJoinName] = useState("");
  const [joinPassword, setJoinPassword] = useState("");
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
  const voiceRecognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const voiceBaseDraftRef = useRef("");
  const lastMessageIdRef = useRef(0);
  const reconnectNowRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const commandEnd = composerValue.lastIndexOf("/");
  const showCommands = commandEnd >= 0 && composerValue.slice(commandEnd).trim().length <= 8 && !composerValue.slice(commandEnd).trim().includes(" ") && composerValue.slice(0, commandEnd).trim() === "";
  const mentionMatch = composerValue.match(/@([^\s@]*)$/);
  const mentionOptions = useMemo(() => room?.members.filter((member) => member.nickname.toLowerCase().includes((mentionMatch?.[1] || "").toLowerCase())) || [], [room?.members, mentionMatch?.[1]]);

  useEffect(() => {
    if (!identity || identity.session_id !== sessionId) return;
    try {
      setComposerValue(localStorage.getItem(`roomai:draft:${sessionId}:${identity.visitor_id}`) || "");
    } catch {
      setComposerValue("");
    }
    setDraftHydrated(true);
  }, [identity?.session_id, identity?.visitor_id, sessionId]);

  useEffect(() => {
    if (!draftHydrated || !identity || identity.session_id !== sessionId) return;
    const key = `roomai:draft:${sessionId}:${identity.visitor_id}`;
    try {
      if (composerValue) localStorage.setItem(key, composerValue);
      else localStorage.removeItem(key);
    } catch {
      // Keep editing available when browser storage is disabled or full.
    }
  }, [composerValue, draftHydrated, identity?.session_id, identity?.visitor_id, sessionId]);

  useEffect(() => {
    setVoiceSupported(Boolean(getSpeechRecognitionConstructor()));
    return () => {
      const recognition = voiceRecognitionRef.current;
      voiceRecognitionRef.current = null;
      recognition?.abort();
    };
  }, [sessionId]);

  useEffect(() => {
    if (room?.closed_at) voiceRecognitionRef.current?.abort();
  }, [room?.closed_at]);

  const applyMessage = useCallback((message: Message) => {
    lastMessageIdRef.current = Math.max(lastMessageIdRef.current, message.id);
    setMessages((current) => {
      const existingIndex = current.findIndex((item) => item.id === message.id);
      if (existingIndex >= 0) return current.map((item, index) => index === existingIndex ? message : item);
      const insertIndex = current.findIndex((item) => item.id > message.id);
      if (insertIndex < 0) return [...current, message];
      return [...current.slice(0, insertIndex), message, ...current.slice(insertIndex)];
    });
  }, []);

  useEffect(() => {
    let canceled = false;
    async function load() {
      lastMessageIdRef.current = 0;
      setDraftHydrated(false);
      setLoading(true);
      setRoomError("");
      setStreamingMessages([]);
      const storedIdentity = loadIdentity(sessionId);
      if (!storedIdentity) {
        if (!canceled) {
          setIdentity(null);
          setRoom(null);
          setMessages([]);
          setComposerValue("");
          setLoading(false);
        }
        return;
      }
      setIdentity(storedIdentity);
      try {
        const [nextRoom, nextMessages, mentions, nextSummary, nextIdeas, nextUsage, nextVotes, nextTodos, nextDocuments] = await Promise.all([
          api.getRoom(sessionId, storedIdentity.visitor_id),
          api.getMessages(sessionId, storedIdentity.visitor_id),
          api.getMentions(sessionId, storedIdentity.visitor_id),
          api.getSummary(sessionId, storedIdentity.visitor_id),
          api.getIdeas(sessionId, storedIdentity.visitor_id),
          api.getUsage(sessionId, storedIdentity.visitor_id).catch(() => emptyUsage),
          api.getVotes(sessionId, storedIdentity.visitor_id),
          api.getTodos(sessionId, storedIdentity.visitor_id),
          api.getDocuments(sessionId, storedIdentity.visitor_id),
        ]);
        if (canceled) return;
        setRoom(nextRoom);
        setMessages(nextMessages);
        lastMessageIdRef.current = nextMessages.reduce((latest, message) => Math.max(latest, message.id), 0);
        setSummary(nextSummary.latest);
        setSummaryVersions(nextSummary.history);
        setIdeas(nextIdeas.ideas);
        setUsage(nextUsage);
        setVotes(nextVotes.votes);
        setTodos(nextTodos.todos);
        setDocuments(nextDocuments.documents);
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

  useEffect(() => {
    const query = search.trim();
    if (!searchOpen || !identity || !query) {
      setSearchResults([]);
      setSearchError("");
      setSearchLoading(false);
      return;
    }
    let canceled = false;
    const timer = window.setTimeout(() => {
      setSearchLoading(true);
      setSearchError("");
      void api.searchRoom(sessionId, identity.visitor_id, query)
        .then((result) => { if (!canceled) setSearchResults(result.results); })
        .catch((reason) => { if (!canceled) setSearchError(reason instanceof Error ? reason.message : "搜索失败，请稍后重试。"); })
        .finally(() => { if (!canceled) setSearchLoading(false); });
    }, 220);
    return () => { canceled = true; window.clearTimeout(timer); };
  }, [identity?.visitor_id, search, searchOpen, sessionId]);

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
    if (!identity || identity.session_id !== sessionId || loading) return;
    const visitorId = identity.visitor_id;
    const nickname = identity.nickname;
    const maxRetries = 5;
    let retryCount = 0;
    let disposed = false;
    let activeSocket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let stableTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (disposed) return;
      setSocketStatus(retryCount ? "reconnecting" : "connecting");
      const socket = new WebSocket(api.socketUrl(sessionId, visitorId));
      activeSocket = socket;
      socketRef.current = socket;
      socket.onopen = () => {
        if (disposed || activeSocket !== socket) return;
        const recovered = retryCount > 0;
        if (stableTimer) clearTimeout(stableTimer);
        stableTimer = setTimeout(() => {
          if (activeSocket === socket) retryCount = 0;
        }, 10_000);
        setSocketStatus("connected");
        if (recovered) showToast("连接已恢复");
        const cursor = lastMessageIdRef.current;
        void api.getMessages(sessionId, visitorId, cursor).then((missedMessages) => {
          missedMessages.forEach((message) => {
            applyMessage(message);
            if (message.visitor_id !== visitorId && message.content.includes(`@${nickname}`)) {
              setUnreadMentionIds((current) => [...new Set([...current, message.id])]);
            }
          });
          if (missedMessages.length && !nearBottomRef.current) setNewMessages(true);
          if (missedMessages.some((message) => message.sender_type === "agent")) {
            void api.getUsage(sessionId, visitorId).then(setUsage).catch(() => undefined);
          }
        }).catch(() => undefined);
      };
      socket.onmessage = (event) => {
        if (disposed || activeSocket !== socket) return;
        try {
          const data = JSON.parse(event.data);
          if (data.type === "message" || data.type === "message_recalled") {
            applyMessage(data.message as Message);
            if (typeof data.stream_id === "string") {
              setStreamingMessages((current) => current.filter((item) => item.stream_id !== data.stream_id));
            }
            if (!nearBottomRef.current) setNewMessages(true);
            const incoming = data.message as Message;
            if (data.type === "message" && incoming.visitor_id !== visitorId && incoming.content.includes(`@${nickname}`)) {
              setUnreadMentionIds((current) => [...new Set([...current, incoming.id])]);
            }
            if (data.type === "message" && incoming.sender_type === "agent") {
              void api.getUsage(sessionId, visitorId).then(setUsage).catch(() => undefined);
            }
          }
          if (data.type === "ai_stream" && typeof data.stream_id === "string") {
            if (data.phase === "start") {
              setStreamingMessages((current) => [...current, { stream_id: data.stream_id, content: "", created_at: new Date().toISOString() }]);
            } else if (data.phase === "delta" && typeof data.content === "string") {
              setStreamingMessages((current) => current.map((item) => item.stream_id === data.stream_id ? { ...item, content: item.content + data.content } : item));
            } else if (data.phase === "end") {
              setStreamingMessages((current) => current.filter((item) => item.stream_id !== data.stream_id));
            }
          }
          if (data.type === "presence") setOnlineIds((data.members as Member[]).map((member) => member.visitor_id));
          if (data.type === "typing" && data.visitor_id !== visitorId) {
            setTypingNames((current) => data.active ? [...new Set([...current, data.nickname])] : current.filter((name) => name !== data.nickname));
            if (data.active && data.visitor_id !== "roomai") window.setTimeout(() => setTypingNames((current) => current.filter((name) => name !== data.nickname)), 3200);
          }
          if (data.type === "phase_changed") setRoom((current) => current ? { ...current, phase: data.phase } : current);
          if (data.type === "model_changed") setRoom((current) => current ? { ...current, model: data.model } : current);
          if (data.type === "red_team_setting") setRoom((current) => current ? { ...current, red_team_enabled: Boolean(data.enabled) } : current);
          if (data.type === "summary_update") {
            const nextSummary = {
              version: data.summary.version,
              content: data.summary,
              through_message_id: data.summary.through_message_id,
              created_at: data.summary.created_at,
            } as SummaryVersion;
            setSummary(nextSummary);
            setSummaryVersions((current) => [nextSummary, ...current.filter((item) => item.version !== nextSummary.version)].slice(0, 20));
            void api.getUsage(sessionId, visitorId).then(setUsage).catch(() => undefined);
          }
          if (data.type === "idea_update") {
            setIdeas((current) => (data.ideas as Idea[]).map((idea) => ({
              ...idea,
              my_stance: current.find((item) => item.id === idea.id)?.my_stance ?? idea.my_stance,
            })));
          }
          if (data.type === "vote_created") setVotes((current) => [...current.filter((vote) => vote.id !== data.vote.id), data.vote as Vote]);
          if (data.type === "vote_update") setVotes((current) => current.map((vote) => vote.id === data.vote.id ? { ...vote, ...(data.vote as Vote), my_option_id: vote.my_option_id } : vote));
          if (data.type === "todo_update") setTodos(data.todos as Todo[]);
          if (data.type === "documents_update") setDocuments(data.documents as RoomDocument[]);
          if (data.type === "room_closed") setRoom((current) => current ? { ...current, closed_at: data.closed_at as string } : current);
          if (data.type === "room_deleted") {
            localStorage.removeItem(`roomai:identity:${sessionId}`);
          localStorage.removeItem(`roomai:draft:${sessionId}:${identity.visitor_id}`);
            navigate("/", { replace: true });
          }
        } catch {
          // Ignore malformed events and keep the chat connection alive.
        }
      };
      socket.onerror = () => socket.close();
      socket.onclose = (event) => {
        if (activeSocket !== socket) return;
        activeSocket = null;
        if (socketRef.current === socket) socketRef.current = null;
        setStreamingMessages([]);
        if (stableTimer) { clearTimeout(stableTimer); stableTimer = null; }
        if (disposed) return;
        if (event.code === 4403) {
          setSocketStatus("disconnected");
          setError("房间连接凭证失效，请刷新后重新加入房间。");
          return;
        }
        if (retryCount >= maxRetries) {
          setSocketStatus("disconnected");
          return;
        }
        const delay = Math.min(500 * (2 ** retryCount), 8000);
        retryCount += 1;
        setSocketStatus("reconnecting");
        retryTimer = setTimeout(connect, delay);
      };
    };

    const reconnectNow = () => {
      retryCount = 0;
      if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
      const previousSocket = activeSocket;
      activeSocket = null;
      if (socketRef.current === previousSocket) socketRef.current = null;
      if (previousSocket) {
        previousSocket.onclose = null;
        previousSocket.close();
      }
      connect();
    };
    reconnectNowRef.current = reconnectNow;
    connect();
    return () => {
      disposed = true;
      reconnectNowRef.current = null;
      if (retryTimer) clearTimeout(retryTimer);
      if (stableTimer) clearTimeout(stableTimer);
      const socket = activeSocket;
      activeSocket = null;
      if (socketRef.current === socket) socketRef.current = null;
      socket?.close();
    };
  }, [applyMessage, identity?.nickname, identity?.session_id, identity?.visitor_id, loading, navigate, sessionId]);

  useEffect(() => {
    if (nearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      setNewMessages(false);
    }
  }, [messages, nearBottom]);

  useEffect(() => {
    if (!nearBottom || !streamingMessages.length) return;
    const frame = window.requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: "auto" }));
    return () => window.cancelAnimationFrame(frame);
  }, [nearBottom, streamingMessages]);

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
      const joined = await api.joinByLink(sessionId, joinName.trim(), joinPassword);
      saveIdentity(joined);
      setIdentity(joined);
      const [nextRoom, nextMessages, mentions, nextSummary, nextIdeas, nextUsage, nextVotes, nextTodos, nextDocuments] = await Promise.all([
        api.getRoom(sessionId, joined.visitor_id), api.getMessages(sessionId, joined.visitor_id), api.getMentions(sessionId, joined.visitor_id),
        api.getSummary(sessionId, joined.visitor_id), api.getIdeas(sessionId, joined.visitor_id), api.getUsage(sessionId, joined.visitor_id).catch(() => emptyUsage),
        api.getVotes(sessionId, joined.visitor_id),
        api.getTodos(sessionId, joined.visitor_id),
        api.getDocuments(sessionId, joined.visitor_id),
      ]);
      setRoom(nextRoom);
      setMessages(nextMessages);
      lastMessageIdRef.current = nextMessages.reduce((latest, message) => Math.max(latest, message.id), 0);
      setSummary(nextSummary.latest);
      setSummaryVersions(nextSummary.history);
      setIdeas(nextIdeas.ideas);
      setUsage(nextUsage);
      setVotes(nextVotes.votes);
      setTodos(nextTodos.todos);
      setDocuments(nextDocuments.documents);
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
    if (anonymousSubmission) {
      sendTyping(false);
      if (typingTimer.current) clearTimeout(typingTimer.current);
      return;
    }
    if (!identity) return;
    sendTyping(true);
    if (typingTimer.current) clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => sendTyping(false), 2800);
  }

  function toggleVoiceInput() {
    const runningRecognition = voiceRecognitionRef.current;
    if (runningRecognition) {
      try {
        runningRecognition.stop();
      } catch {
        runningRecognition.abort();
        voiceRecognitionRef.current = null;
        setVoiceListening(false);
      }
      return;
    }
    const SpeechRecognition = getSpeechRecognitionConstructor();
    if (!SpeechRecognition) {
      setError("当前浏览器暂不支持语音输入，请使用键盘输入。");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    voiceBaseDraftRef.current = composerValue.trim()
      ? `${composerValue.trimEnd()}\n`
      : "";
    recognition.onresult = (event) => {
      const transcript = Array.from(
        { length: event.results.length },
        (_, index) => event.results[index]?.[0]?.transcript || "",
      ).join("").trimStart();
      const availableLength = Math.max(0, 2000 - voiceBaseDraftRef.current.length);
      setComposerValue(`${voiceBaseDraftRef.current}${[...transcript].slice(0, availableLength).join("")}`);
    };
    recognition.onerror = (event) => {
      const messages: Record<string, string> = {
        "not-allowed": "浏览器未授予麦克风或语音识别权限，请检查站点权限。",
        "service-not-allowed": "浏览器未开放语音识别服务，请检查浏览器设置。",
        "no-speech": "没有听到语音，请重试。",
        network: "语音识别服务连接失败，请检查网络后重试。",
        "language-not-supported": "当前浏览器的语音识别服务不支持中文。",
      };
      if (voiceRecognitionRef.current === recognition) voiceRecognitionRef.current = null;
      setError(messages[event.error || ""] || "语音输入暂时不可用，请改用键盘输入。");
      setVoiceListening(false);
    };
    recognition.onend = () => {
      if (voiceRecognitionRef.current === recognition) voiceRecognitionRef.current = null;
      setVoiceListening(false);
    };
    voiceRecognitionRef.current = recognition;
    setError("");
    setVoiceListening(true);
    try {
      recognition.start();
    } catch {
      voiceRecognitionRef.current = null;
      setVoiceListening(false);
      setError("无法启动语音输入，请检查浏览器麦克风权限后重试。");
    }
  }

  async function submitMessage(event?: FormEvent) {
    event?.preventDefault();
    if (!identity || room?.closed_at || sending || voiceListening || (!composerValue.trim() && !selectedImage)) return;
    if (/^\/结束(?:\s|$)/.test(composerValue.trim())) {
      if (!identity.owner_token) setError("只有房主可以结束讨论。");
      else await startFinishReview();
      return;
    }
    setSending(true);
    setError("");
    try {
      let image_url: string | undefined;
      if (selectedImage) image_url = (await api.uploadImage(sessionId, identity.visitor_id, selectedImage)).image_url;
      await api.sendMessage(sessionId, {
        visitor_id: identity.visitor_id,
        content: composerValue.trim(),
        reply_to_message_id: anonymousSubmission ? undefined : replyTo?.id,
        image_url,
        anonymous: anonymousSubmission,
      });
      setComposerValue("");
      setReplyTo(null);
      setSelectedImage(null);
      setAnonymousSubmission(false);
      if (composerRef.current) composerRef.current.style.height = "auto";
      setNearBottom(true);
      sendTyping(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败，请重试");
    } finally {
      setSending(false);
    }
  }

  useEffect(() => {
    if (room && room.phase !== "diverge" && anonymousSubmission) setAnonymousSubmission(false);
  }, [room?.phase, anonymousSubmission]);

  async function uploadRoomDocument(file?: File) {
    if (!identity || !file || documentUploading) return;
    if (file.size > 10 * 1024 * 1024) {
      setError("资料文件不能超过 10MB");
      return;
    }
    setDocumentUploading(true);
    setError("");
    try {
      const result = await api.uploadDocument(sessionId, identity.visitor_id, file);
      setDocuments((current) => [result.document, ...current.filter((item) => item.id !== result.document.id)]);
      setToast(`已添加「${result.document.filename}」，提问时会检索相关段落`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "添加资料失败，请重试");
    } finally {
      setDocumentUploading(false);
    }
  }

  async function removeRoomDocument(document: RoomDocument) {
    if (!identity || !window.confirm(`删除资料「${document.filename}」？RoomAI 将不再从中检索内容。`)) return;
    try {
      await api.deleteDocument(sessionId, identity, document.id);
      setDocuments((current) => current.filter((item) => item.id !== document.id));
      setToast("已删除讨论资料");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除资料失败，请重试");
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
    if (!room || room.closed_at || !identity?.owner_token) return;
    const phase = room.phase === "diverge" ? "converge" : "diverge";
    try {
      await api.setPhase(sessionId, identity, phase);
      setRoom({ ...room, phase });
    } catch (err) { setError(err instanceof Error ? err.message : "切换阶段失败"); }
  }

  async function changeModel(model: string) {
    if (!identity || !room || room.closed_at || !identity.owner_token || modelSaving) return;
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

  async function toggleRedTeam() {
    if (!identity?.owner_token || !room || room.closed_at) return;
    try {
      const result = await api.setRedTeam(sessionId, identity, !room.red_team_enabled);
      setRoom((current) => current ? { ...current, red_team_enabled: result.enabled } : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新红队设置失败");
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

  async function voteIdea(ideaId: number, stance: "support" | "oppose") {
    if (!identity || room?.closed_at || ideaVoteBusyIds.includes(ideaId)) return;
    setIdeaVoteBusyIds((current) => [...current, ideaId]);
    try {
      const result = await api.voteIdea(sessionId, ideaId, identity.visitor_id, stance);
      setIdeas(result.ideas);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新想法投票失败");
    } finally {
      setIdeaVoteBusyIds((current) => current.filter((id) => id !== ideaId));
    }
  }

  async function updateIdeaStatus(ideaId: number, status: IdeaStatus) {
    if (!identity?.owner_token || room?.closed_at) return;
    try {
      const result = await api.updateIdeaStatus(sessionId, identity, ideaId, status);
      setIdeas(result.ideas);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新想法状态失败");
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

  async function toggleTodo(todo: Todo) {
    if (!identity || room?.closed_at || todoBusyIds.includes(todo.id)) return;
    setTodoBusyIds((current) => [...current, todo.id]);
    try {
      const result = await api.updateTodo(sessionId, identity.visitor_id, {
        id: todo.id,
        title: todo.title,
        assignee: todo.assignee,
        status: todo.status === "done" ? "open" : "done",
        due_at: todo.due_at,
      });
      setTodos((current) => current.map((item) => item.id === result.todo.id ? result.todo : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新待办状态失败");
    } finally {
      setTodoBusyIds((current) => current.filter((id) => id !== todo.id));
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

  function selectSearchResult(result: RoomSearchResult) {
    setSearchOpen(false);
    setSearch("");
    if (result.type === "report") {
      navigate(`/room/${sessionId}/memo`);
      return;
    }
    if (result.source_message_id) {
      window.setTimeout(() => jumpToMessage(result.source_message_id!), 80);
    } else {
      showToast("这条结果没有对应的原始消息位置。");
    }
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
    if (command === "/结束") {
      if (!identity?.owner_token) {
        setError("只有房主可以结束讨论。");
        return;
      }
      await startFinishReview();
      return;
    }
    setComposerValue(`${command} `);
    composerRef.current?.focus();
  }

  async function startFinishReview() {
    if (!identity?.owner_token || !room || room.closed_at) return;
    setComposerValue("");
    setFinishReviewOpen(true);
    setFinishReview(null);
    setFinishReviewLoading(true);
    setError("");
    try {
      setFinishReview(await api.reviewFinish(sessionId, identity));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "维度检查暂时失败，请稍后重试。");
      setFinishReviewOpen(false);
    } finally {
      setFinishReviewLoading(false);
    }
  }

  async function confirmFinishRoom() {
    if (!identity?.owner_token || !finishReview) return;
    setFinishSaving(true);
    setError("");
    try {
      const report = await api.finishRoom(sessionId, identity, finishReview.missing_dimensions.map((item) => item.key));
      setRoom((current) => current ? { ...current, closed_at: report.created_at } : current);
      setFinishReviewOpen(false);
      setSettingsOpen(false);
      navigate(`/room/${sessionId}/memo`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "纪要生成失败，请重试。");
    } finally {
      setFinishSaving(false);
    }
  }

  async function confirmDeleteRoom() {
    if (!identity?.owner_token || !room || roomDeleting) return;
    setRoomDeleting(true);
    setRoomDeleteError("");
    try {
      await api.deleteRoom(sessionId, identity);
      try {
        localStorage.removeItem(`roomai:identity:${sessionId}`);
        localStorage.removeItem(`roomai:draft:${sessionId}:${identity.visitor_id}`);
      } catch {
        // Removing stale local credentials is best-effort after the server deletes the room.
      }
      setRoomDeleteOpen(false);
      setSettingsOpen(false);
      navigate("/", { replace: true });
    } catch (reason) {
      setRoomDeleteError(reason instanceof Error ? reason.message : "删除房间失败，请重试。");
    } finally {
      setRoomDeleting(false);
    }
  }

  function selectMention(member: Member) {
    const current = composerValue.replace(/@[^\s@]*$/, `@${member.nickname} `);
    setComposerValue(current);
    composerRef.current?.focus();
  }

  function insertSuggestedMention(nickname: string) {
    if (!room?.members.some((member) => member.nickname === nickname)) return;
    setComposerValue((current) => `${current.trim()}${current.trim() ? " " : ""}@${nickname} `);
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
      <label className="field">房间密码（如有）<input type="password" autoComplete="current-password" maxLength={72} value={joinPassword} onChange={(event) => setJoinPassword(event.target.value)} placeholder="房间设置了密码时填写" /></label>
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
        {!!summary.content.decision_points?.length && <div className="summary-sub summary-decisions"><b>待团队拍板</b>{summary.content.decision_points.slice(0, 3).map((item, index) => <span key={`${index}-${item}`}>{item}</span>)}</div>}
        {summary.content.todos.length > 0 && <div className="summary-sub"><b>下一步</b><span>{summary.content.todos[0].title}{summary.content.todos[0].assignee ? ` · ${summary.content.todos[0].assignee}` : ""}</span></div>}
      </> : <p className="side-empty">讨论开始后，RoomAI 会在这里整理共识、分歧和待办。</p>}

      <div className="side-label-row todo-heading"><p className="side-label">待办</p><span className="count">{todos.filter((todo) => todo.status === "open").length} 未完成</span></div>
      {todos.length ? <div className="todo-list">{todos.slice(0, 6).map((todo) => <button className={`todo-item ${todo.status !== "open" ? "todo-item-closed" : ""}`} key={todo.id} disabled={Boolean(room.closed_at)} onClick={() => setEditingTodo(todo)}>
        <span className="todo-state-mark">{todo.status === "done" ? "✓" : todo.status === "dropped" ? "–" : "○"}</span>
        <span className="todo-item-copy"><b>{todo.title}</b><small>{todo.assignee || "待分配"} · {todo.status === "open" ? "未完成" : todo.status === "done" ? "已完成" : "已取消"}{todo.due_at ? ` · ${todo.due_at} 截止` : ""}</small></span>
      </button>)}</div> : <p className="side-empty">讨论中明确的下一步会整理到这里。</p>}

      <div className="side-label-row"><p className="side-label">想法池</p><span className="count">{ideas.length}</span></div>
      {ideas.length ? <div className="idea-list">{ideas.slice(0, 5).map((idea) => <div className="idea-item" key={idea.id}>
        <button type="button" className="idea-item-main" onClick={() => idea.source_message_id && jumpToMessage(idea.source_message_id)}>
          <span className="idea-item-title">{idea.title}</span>
          <span className="idea-item-meta">{idea.average_score !== null ? `${idea.average_score.toFixed(1)} / 5` : "待评估"}{idea.proposer_name ? ` · ${idea.proposer_name}` : ""}{idea.supplementers.length ? ` · 补充：${idea.supplementers.join("、")}` : ""}</span>
          <span className="idea-status-label">{ideaStatusLabels[idea.status]}</span>
        </button>
        <div className="idea-item-controls">
          <button type="button" className={`idea-vote-button ${idea.my_stance === "support" ? "idea-vote-active" : ""}`} aria-pressed={idea.my_stance === "support"} disabled={Boolean(room.closed_at) || ideaVoteBusyIds.includes(idea.id)} onClick={() => void voteIdea(idea.id, "support")}><ThumbsUp size={12} />支持 {idea.support_count}</button>
          <button type="button" className={`idea-vote-button ${idea.my_stance === "oppose" ? "idea-vote-active" : ""}`} aria-pressed={idea.my_stance === "oppose"} disabled={Boolean(room.closed_at) || ideaVoteBusyIds.includes(idea.id)} onClick={() => void voteIdea(idea.id, "oppose")}><ThumbsDown size={12} />反对 {idea.oppose_count}</button>
          {identity.owner_token && <select aria-label={`设置“${idea.title}”的状态`} value={idea.status} disabled={Boolean(room.closed_at)} onChange={(event) => void updateIdeaStatus(idea.id, event.target.value as IdeaStatus)}>
            <option value="considering">讨论中</option><option value="pending">待定</option><option value="adopted">已采纳</option><option value="rejected">已否决</option>
          </select>}
        </div>
      </div>)}</div> : <p className="side-empty">值得做的想法，会在讨论中浮现。</p>}

      <div className="side-label-row document-heading">
        <p className="side-label">讨论资料</p>
        <span className="document-heading-actions"><span className="count">{documents.length}/20</span><label className={`icon-btn attach document-add ${room.closed_at || documents.length >= 20 || documentUploading ? "disabled" : ""}`} title={room.closed_at ? "讨论已结束" : documents.length >= 20 ? "每个房间最多 20 份资料" : "添加讨论资料"}><FilePlus2 size={14} /><input type="file" disabled={Boolean(room.closed_at) || documents.length >= 20 || documentUploading} accept=".pdf,.docx,.pptx,.txt,.md,application/pdf,text/plain" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadRoomDocument(file); event.currentTarget.value = ""; }} /></label></span>
      </div>
      {documents.length ? <div className="document-list">{documents.map((item) => <div className="document-item" key={item.id}>
        <FileText size={14} className="document-icon" />
        <span className="document-copy" title={item.filename}><b>{item.filename}</b><small>{formatFileSize(item.size)} · {item.chunk_count} 段 · {item.uploaded_by}</small></span>
        {(identity.owner_token || item.uploaded_by === identity.nickname) && !room.closed_at && <button className="icon-btn document-delete" onClick={() => void removeRoomDocument(item)} aria-label={`删除资料 ${item.filename}`} title="删除资料"><Trash2 size={13} /></button>}
      </div>)}</div> : <p className="side-empty">添加 PDF、Word、PPT、TXT 或 Markdown，RoomAI 会按问题检索相关内容。</p>}

      <p className="side-label">讨论维度</p>
      <div className="dim-list">{dimensionLabels.map(([label, key]) => {
        const covered = Boolean(summary?.content.dimension_coverage[key]);
        return <div className={`dim-row ${covered ? "dim-done" : ""}`} key={key}><span className="dim-dot" />{label}<span>{covered ? "已讨论" : "待聊"}</span></div>;
      })}</div>
      {summary?.content.atmosphere && <div className={`atmosphere-card atmosphere-${summary.content.atmosphere.state}`}>
        <div className="atmosphere-card-head"><b>讨论氛围</b><span>{atmosphereLabels[summary.content.atmosphere.state]} · {Math.round(summary.content.atmosphere.confidence * 100)}%</span></div>
        <p>{summary.content.atmosphere.evidence || "目前没有足够明显的互动信号。"}</p>
        {summary.content.atmosphere.adjustment && <small>RoomAI 会：{summary.content.atmosphere.adjustment}</small>}
        <small className="atmosphere-footnote">依据群体发言形态估计，不判断个人情绪</small>
      </div>}
      {summary?.content.phase_suggestion === "converge" && room.phase === "diverge" && <div className="phase-suggestion"><span>RoomAI 建议开始收敛</span><button disabled={!identity.owner_token} onClick={() => void togglePhase()}>{identity.owner_token ? "切换阶段" : "等待房主确认"}</button></div>}

      <a className="sidebar-replay-link" href={`/room/${sessionId}/replay`}><Clock3 size={15} /><span>讨论回放</span><ArrowRight size={13} /></a>
      {room.closed_at && <a className="sidebar-replay-link" href={`/room/${sessionId}/memo`}><FileText size={15} /><span>立项纪要</span><ArrowRight size={13} /></a>}
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
        <button className={`phase-btn ${room.phase === "converge" ? "phase-converge" : ""}`} onClick={togglePhase} disabled={!identity.owner_token || Boolean(room.closed_at)} title={identity.owner_token ? "点击切换讨论阶段" : "发散阶段"}><i />{room.phase === "diverge" ? "发散" : "收敛"}</button>
        {socketStatus !== "connected" && <>{socketStatus === "disconnected" ? <button className="connection-status connection-offline" onClick={() => reconnectNowRef.current?.()}>重试连接</button> : <span className="connection-status" aria-live="polite">{socketStatus === "connecting" ? "连接中…" : "重连中…"}</span>}</>}
        <div className="online" title={`${onlineMembers.length} 人在线`}><span className="online-stack">{onlineMembers.slice(0, 3).map((member) => <i key={member.visitor_id}>{member.nickname.slice(0, 1)}</i>)}</span><span>{onlineMembers.length} 在线</span></div>
        <span className="top-sep" />
        {unreadMentionIds.length > 0 && <button className="mention-badge" onClick={() => void openUnreadMention()} title="跳转到未读 @ 提醒"><AtSign size={13} /><span>{unreadMentionIds.length}</span></button>}
        <button className={`icon-btn ${searchOpen ? "active" : ""}`} onClick={() => { setSearchOpen((value) => !value); setSearch(""); }} aria-label="搜索消息"><Search size={17} /></button>
        <button className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="房间设置" title="房间设置"><Settings2 size={17} /></button>
        <button className="icon-btn" onClick={copyInvite} aria-label="复制邀请链接" title="复制邀请链接">{linkCopied ? <ClipboardCheck size={17} /> : <Clipboard size={17} />}</button>
      </header>

      {searchOpen && <>
        <div className="searchbar"><Search size={16} /><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索消息、想法或总结…" /><span>{searchLoading ? "搜索中…" : searchError || (search.trim() ? `${searchResults.length} 条结果` : "输入关键词搜索")}</span><button className="icon-btn" onClick={() => { setSearchOpen(false); setSearch(""); }} aria-label="关闭搜索"><X size={15} /></button></div>
        {search.trim() && <div className="search-results" aria-label="房间搜索结果">
          {searchLoading && !searchResults.length ? <p className="search-empty">正在搜索消息、想法、总结和纪要…</p> : searchError ? <p className="search-empty search-empty-error">{searchError}</p> : searchResults.length ? searchResults.map((result) => <button className="search-result" key={`${result.type}-${result.id}`} onClick={() => selectSearchResult(result)}>
            <span className={`search-result-type search-result-${result.type}`}>{result.type === "message" ? "消息" : result.type === "idea" ? "想法" : result.type === "report" ? "纪要" : "总结"}</span>
            <span className="search-result-copy"><b>{result.title}{result.type === "idea" && result.score != null ? ` · ${result.score.toFixed(1)}/5` : ""}</b><small>{result.excerpt || "没有可显示的摘要"}</small></span>
            <time>{formatTime(result.created_at)}</time>
          </button>) : !searchLoading && <p className="search-empty">没有找到匹配内容。</p>}
        </div>}
      </>}

      <section className="chat">
        {room.closed_at && <a className="room-closed-banner" href={`/room/${sessionId}/memo`}><Check size={15} />讨论已结束，立项纪要已生成<span>查看纪要 <ArrowRight size={13} /></span></a>}
        {room.prep_checklist.length > 0 && <div className="prep">
          <button className="prep-head" onClick={() => setPrepOpen((value) => !value)}><ChevronDown size={15} className={prepOpen ? "open" : ""} />开始前想一想<span>{room.prep_checklist.length} 个问题</span></button>
          {prepOpen && <ol className="prep-body">{room.prep_checklist.map((question) => <li key={question}>{question}</li>)}</ol>}
        </div>}

        <div className="msg-list" ref={listRef} onScroll={onScroll}>
          <div className="msg-start">讨论从这里开始</div>
          {filteredMessages.map((message, index) => {
            const own = message.visitor_id === identity.visitor_id;
            const isAgentMessage = message.sender_type === "agent" || message.sender_name === "RoomAI";
            const previous = filteredMessages[index - 1];
            const showAuthor = !previous || previous.sender_name !== message.sender_name || new Date(message.created_at).getTime() - new Date(previous.created_at).getTime() > 5 * 60 * 1000;
            const showDateDivider = !previous || localDayKey(previous.created_at) !== localDayKey(message.created_at);
            const messageTodos = (message.todo_ids || []).map((todoId) => todos.find((todo) => todo.id === todoId)).filter((todo): todo is Todo => Boolean(todo));
            const mentioned = !own && message.content.includes(`@${identity.nickname}`);
            const mentionSuggestion = isAgentMessage ? message.content.match(/\[\[suggest_mention:([^\]]+)\]\]\s*$/) : null;
            const visibleContent = mentionSuggestion ? message.content.replace(/\n?\[\[suggest_mention:[^\]]+\]\]\s*$/, "").trim() : message.content;
            return <Fragment key={message.id}>
            {showDateDivider && <div className="date-divider"><span>{formatDayDivider(message.created_at)}</span></div>}
            <article id={`message-${message.id}`} className={`msg ${own ? "msg-own" : ""} ${isAgentMessage ? "msg-agent" : ""} ${mentioned ? "msg-mention" : ""} ${quoteFlash === message.id ? "msg-flash" : ""} ${menu?.message.id === message.id ? "msg-open" : ""}`}>
              <div className={`avatar msg-avatar ${isAgentMessage ? "avatar-ai" : ""}`}>{isAgentMessage ? <Sparkles size={13} /> : message.sender_name.slice(0, 1)}</div>
              <div className="msg-main">
                {showAuthor && <div className="msg-meta"><b className={isAgentMessage ? "agent-name" : ""}>{message.sender_name}</b>{isAgentMessage && <i className="tag-ai">AI</i>}<time>{formatTime(message.created_at)}</time></div>}
                <div className={`msg-body ${pressingId === message.id ? "msg-pressing" : ""}`} onPointerDown={(event) => startPress(message, event)} onPointerUp={cancelPress} onPointerLeave={cancelPress} onPointerMove={handlePressMove} onContextMenu={(event) => { event.preventDefault(); if (!message.is_recalled) openMenu(message, event.clientX, event.clientY); }}>
                  {message.reply_to && <button className="quote" onClick={() => jumpToMessage(message.reply_to!.id)}><span><b>{message.reply_to.sender_name}</b><small>{message.reply_to.content}</small></span></button>}
                  {message.is_recalled ? <span className="msg-recalled">这条消息已撤回</span> : <>
                    {visibleContent && (isAgentMessage
                      ? <MarkdownContent content={visibleContent} />
                      : <p className="msg-text">{visibleContent.split(/(@[\w\u4e00-\u9fff-]+)/g).map((part, partIndex) => part.startsWith("@") ? <mark className="mention-mark" key={partIndex}>{part}</mark> : part)}</p>)}
                    {mentionSuggestion && room.members.some((member) => member.nickname === mentionSuggestion[1]) && <button className="smart-mention" type="button" onClick={() => insertSuggestedMention(mentionSuggestion[1])}>建议问问 <b>@{mentionSuggestion[1]}</b> <ArrowRight size={12} /></button>}
                    {message.image_url && <button className="msg-img" onClick={() => window.open(api.imageUrl(message.image_url!), "_blank", "noopener,noreferrer")}><img src={api.imageUrl(message.image_url)} alt="讨论附件" /></button>}
                  </>}
                </div>
                {messageTodos.length > 0 && <TodoCard todos={messageTodos} disabled={Boolean(room.closed_at)} busyIds={todoBusyIds} onToggle={(todo) => void toggleTodo(todo)} onEdit={setEditingTodo} />}
                {votesByMessage.has(message.id) && <VoteCard vote={votesByMessage.get(message.id)!} isOwner={Boolean(identity.owner_token)} busy={voteBusyIds.includes(votesByMessage.get(message.id)!.id)} onVote={(voteId, optionId) => void castVote(voteId, optionId)} onClose={(voteId) => void closeVote(voteId)} />}
              </div>
            </article>
            </Fragment>;
          })}
          {streamingMessages.filter((item) => item.content).map((item) => <article className="msg msg-agent-streaming" key={item.stream_id}>
            <div className="avatar msg-avatar avatar-ai"><Sparkles size={13} /></div>
            <div className="msg-main"><div className="msg-meta"><b className="agent-name">RoomAI</b><i className="tag-ai">AI</i><time>{formatTime(item.created_at)}</time></div>
              <div className="msg-body"><MarkdownContent content={item.content} /><i className="stream-cursor" aria-hidden="true" /></div>
            </div>
          </article>)}
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
              <label className={`icon-btn attach ${room.closed_at ? "disabled" : ""}`} title={room.closed_at ? "讨论已结束" : "添加图片"}><ImagePlus size={17} /><input type="file" disabled={Boolean(room.closed_at)} accept="image/jpeg,image/png,image/webp,image/gif" onChange={(event) => { const file = event.target.files?.[0]; if (file && file.size <= 5 * 1024 * 1024) setSelectedImage(file); else if (file) setError("图片大小不能超过 5MB"); event.currentTarget.value = ""; }} /></label>
              {room.phase === "diverge" && <button type="button" className={`icon-btn anonymous-toggle ${anonymousSubmission ? "anonymous-toggle-active" : ""}`} title="匿名头脑风暴；消息对全房可见，但不显示你的身份" aria-label="匿名提想法" aria-pressed={anonymousSubmission} disabled={Boolean(room.closed_at) || voiceListening} onClick={() => { const next = !anonymousSubmission; if (next) { sendTyping(false); if (typingTimer.current) clearTimeout(typingTimer.current); setSelectedImage(null); setReplyTo(null); } setAnonymousSubmission(next); }}><EyeOff size={16} /></button>}
              <button type="button" className="icon-btn" title="快捷指令" disabled={Boolean(room.closed_at) || voiceListening} onClick={() => { setComposerValue("/"); composerRef.current?.focus(); }}><Command size={16} /></button>
              <button type="button" className={`icon-btn voice-btn ${voiceListening ? "voice-listening" : ""}`} title={!voiceSupported ? "当前浏览器不支持语音识别" : voiceListening ? "结束语音输入" : "中文语音输入；部分浏览器可能会联网处理音频"} aria-label={voiceListening ? "结束语音输入" : "开始中文语音输入"} aria-pressed={voiceListening} disabled={Boolean(room.closed_at) || !voiceSupported} onClick={toggleVoiceInput}><Mic size={16} /></button>
            </div>
            <div className="composer-input">
              {showCommands && <div className="suggest suggest-cmd">{commands.filter((command) => command.startsWith(composerValue.slice(commandEnd))).map((command) => <button type="button" onClick={() => void selectCommand(command)} key={command}><span>{command}</span><small>RoomAI 快捷指令</small></button>)}</div>}
              {mentionMatch && mentionOptions.length > 0 && <div className="suggest suggest-mention"><button type="button" onClick={selectRoomAI}><span className="avatar avatar-sm avatar-ai"><Sparkles size={12} /></span><b>RoomAI</b><small>AI 助手</small></button>{mentionOptions.map((member) => <button type="button" onClick={() => selectMention(member)} key={member.visitor_id}><span className="avatar avatar-sm">{member.nickname.slice(0, 1)}</span><b>{member.nickname}</b>{member.is_owner ? <small>房主</small> : null}</button>)}</div>}
              <textarea ref={composerRef} rows={1} maxLength={2000} value={composerValue} disabled={Boolean(room.closed_at) || voiceListening} onChange={(event) => { handleTyping(event.target.value); event.currentTarget.style.height = "auto"; event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 140)}px`; }} onKeyDown={onComposerKey} placeholder={room.closed_at ? "讨论已结束" : voiceListening ? "正在聆听…点击麦克风结束后可编辑" : anonymousSubmission ? "写下匿名想法；所有成员都能看到内容，但看不到你的身份" : "说说你的想法…（输入 / 查看指令，@ 提及伙伴）"} />
            </div>
            <button className="send-btn" disabled={Boolean(room.closed_at) || sending || voiceListening || (!composerValue.trim() && !selectedImage)} aria-label="发送消息">{sending ? <LoaderCircle size={17} className="spin" /> : <Send size={16} />}</button>
          </form>
          <div className="composer-hint"><span>{anonymousSubmission ? "匿名头脑风暴：发散阶段开放，身份不会显示" : voiceListening ? "正在聆听，点击麦克风结束" : voiceSupported ? "语音识别由浏览器提供，部分浏览器可能会联网处理音频" : "图片最大 5MB"}</span><span className="composer-shortcuts"><kbd>Enter</kbd> 发送 · <kbd>Shift + Enter</kbd> 换行</span></div>
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
          <select id="model-select" value={room.model} disabled={!identity.owner_token || Boolean(room.closed_at) || modelSaving || room.available_models.length < 2} onChange={(event) => void changeModel(event.target.value)}>
            {room.available_models.map((model) => <option key={model} value={model}>{model}</option>)}
          </select>
          <p className="field-note">{identity.owner_token ? "切换后仅影响之后的新调用。" : "只有房主可以切换模型。"}</p>
        </div>
        <div className="settings-block">
          <p className="field">红队质疑</p>
          <button className="red-team-setting" disabled={!identity.owner_token || Boolean(room.closed_at)} onClick={() => void toggleRedTeam()}>
            <span><b>{room.red_team_enabled ? "已开启" : "已关闭"}</b><small>收敛阶段自动审查最多 {room.red_team_count}/3 次；也可用 /挑战 手动审查。</small></span>
            <span className={`setting-switch ${room.red_team_enabled ? "setting-switch-on" : ""}`} aria-hidden="true"><i /></span>
          </button>
          {!identity.owner_token && <p className="field-note">只有房主可以修改红队设置。</p>}
        </div>
        <div className="settings-usage"><b>本次讨论用量</b><span>{(usage?.total_tokens || 0).toLocaleString("zh-CN")} tokens</span><small>{usage?.cost_configured ? `估算费用 ¥${usage.cost_estimate.toFixed(4)}` : "费用价格未配置；可在 .env 设置模型单价。"}</small></div>
        <div className="settings-block">
          <button className="btn btn-block" onClick={() => void copyInvite()}>{linkCopied ? <ClipboardCheck size={15} /> : <Clipboard size={15} />} {linkCopied ? "已复制邀请链接" : "复制邀请链接"}</button>
          <p className="field-note">房主凭证保存在当前浏览器，清除浏览器数据后将无法管理房间。</p>
        </div>
        <div className="settings-block">
          {room.closed_at ? <a className="btn btn-primary btn-block" href={`/room/${sessionId}/memo`}><FileText size={15} />查看立项纪要</a> : identity.owner_token ? <button className="btn btn-primary btn-block" onClick={() => void startFinishReview()}><Check size={15} />结束讨论并生成纪要</button> : <p className="field-note">只有房主可以结束讨论并生成纪要。</p>}
        </div>
        {identity.owner_token && <div className="settings-block danger-zone">
          <button className="btn btn-block btn-danger-outline" onClick={() => { setRoomDeleteError(""); setSettingsOpen(false); setRoomDeleteOpen(true); }}><Trash2 size={15} />删除房间</button>
          <p className="field-note">永久删除房间、消息、讨论资料和图片，删除后无法恢复。</p>
        </div>}
      </section>
    </div>}
    {roomDeleteOpen && <div className="overlay" onClick={() => !roomDeleting && setRoomDeleteOpen(false)}>
      <section className="modal room-delete-modal" role="dialog" aria-modal="true" aria-labelledby="room-delete-title" onClick={(event) => event.stopPropagation()}>
        <button className="icon-btn modal-close" disabled={roomDeleting} onClick={() => setRoomDeleteOpen(false)} aria-label="取消删除"><X size={17} /></button>
        <p className="eyebrow">永久删除</p>
        <h2 id="room-delete-title">删除「{room.topic}」？</h2>
        <p className="room-delete-warning">这会永久清除房间里的消息、纪要、投票、图片和讨论资料，邀请链接也会立即失效。此操作无法撤销。</p>
        {roomDeleteError && <p className="form-error">{roomDeleteError}</p>}
        <div className="room-delete-actions"><button className="btn" disabled={roomDeleting} onClick={() => { setRoomDeleteOpen(false); setSettingsOpen(true); }}>保留房间</button><button className="btn btn-danger" disabled={roomDeleting} onClick={() => void confirmDeleteRoom()}>{roomDeleting ? <><span className="btn-spinner" />正在删除…</> : <><Trash2 size={15} />永久删除房间</>}</button></div>
      </section>
    </div>}
    {finishReviewOpen && <div className="overlay" onClick={() => !finishSaving && setFinishReviewOpen(false)}>
      <section className="modal finish-review-modal" role="dialog" aria-modal="true" aria-labelledby="finish-review-title" onClick={(event) => event.stopPropagation()}>
        <button className="icon-btn modal-close" onClick={() => setFinishReviewOpen(false)} aria-label="返回讨论"><X size={17} /></button>
        <p className="eyebrow">结束前检查</p>
        <h2 id="finish-review-title">立项维度查漏</h2>
        <p className="modal-sub">RoomAI 已对照五个维度检查当前讨论。你可以返回继续补充，也可以确认结束并生成纪要。</p>
        {finishReviewLoading ? <div className="finish-review-loading"><span className="btn-spinner" />正在检查讨论记录…</div> : finishReview && <>
          <div className="finish-dimensions">{dimensionLabels.map(([label, key]) => <div className="finish-dimension" key={key}><span className={finishReview.coverage[key] ? "finish-covered" : "finish-missing"}>{finishReview.coverage[key] ? "✓" : "○"}</span><b>{label}</b><small>{finishReview.coverage[key] ? "已覆盖" : "待补充"}</small></div>)}</div>
          {finishReview.missing_dimensions.length > 0 ? <div className="finish-missing-list"><b>纪要会附上这些待补充问题</b>{finishReview.missing_dimensions.map((item) => <p key={item.key}><strong>{item.label}</strong>{item.question}</p>)}</div> : <p className="finish-all-covered">五个立项维度均已覆盖，可以生成纪要。</p>}
          <div className="finish-review-actions"><button className="btn" disabled={finishSaving} onClick={() => setFinishReviewOpen(false)}>返回讨论</button><button className="btn btn-primary" disabled={finishSaving} onClick={() => void confirmFinishRoom()}>{finishSaving ? <><span className="btn-spinner" />正在生成…</> : <><Check size={15} />确认结束并生成纪要</>}</button></div>
        </>}
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

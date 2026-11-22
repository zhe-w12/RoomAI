export type Identity = {
  session_id: string;
  visitor_id: string;
  nickname: string;
  owner_token?: string;
};

export type Member = { visitor_id: string; nickname: string; is_owner: number };

export type Room = {
  session_id: string;
  topic: string;
  invite_code: string;
  model: string;
  available_models: string[];
  phase: "diverge" | "converge";
  prep_checklist: string[];
  members: Member[];
  unread_mentions: number;
  created_at: string;
  closed_at: string | null;
  red_team_enabled: boolean;
  red_team_count: number;
};

export type RoomReport = {
  minutes_markdown: string;
  retrospective_markdown: string;
  missing_dimensions: { key: string; label: string; question: string }[];
  created_at: string;
};

export type FinishReview = {
  coverage: Record<string, boolean>;
  missing_dimensions: { key: string; label: string; question: string }[];
};

export type SummaryContent = {
  consensus: string[];
  disagreements: string[];
  todos: { title: string; assignee?: string }[];
  dimension_coverage: Record<string, boolean>;
  guide_question?: string;
  phase_suggestion?: "converge" | null;
};

export type SummaryVersion = {
  version: number;
  content: SummaryContent;
  through_message_id: number;
  created_at: string;
};

export type Idea = {
  id: number;
  title: string;
  description: string;
  proposer_name: string;
  source_message_id: number | null;
  status: string;
  dimensions: Record<string, number>;
  rationale: string;
  average_score: number | null;
};

export type RoomUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_estimate: number;
  cost_configured: boolean;
};

export type Todo = {
  id: number;
  session_id: string;
  title: string;
  assignee: string;
  status: "open" | "done" | "dropped";
  due_at: string | null;
  source_message_id: number | null;
  created_at: string;
  updated_at: string;
};

export type ReplayEvent = {
  id: string;
  type: "message" | "summary" | "idea" | "evaluation" | "vote" | "decision" | "todo";
  title: string;
  detail: string;
  created_at: string;
  message_id: number | null;
};

export type Message = {
  id: number;
  session_id: string;
  visitor_id: string | null;
  sender_name: string;
  sender_type: "user" | "agent";
  content: string;
  reply_to: { id: number; sender_name: string; content: string } | null;
  image_url: string | null;
  is_recalled: number;
  created_at: string;
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...init?.headers },
  });
  if (!response.ok) {
    let detail = "请求失败，请稍后重试";
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      // The server did not return a JSON error body.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  createRoom: (topic: string, nickname: string) =>
    request<Identity & { invite_code: string; opening_message: Message }>("/api/rooms", {
      method: "POST",
      body: JSON.stringify({ topic, nickname }),
    }),
  joinByCode: (invite_code: string, nickname: string) =>
    request<Identity>("/api/rooms/join", { method: "POST", body: JSON.stringify({ invite_code, nickname }) }),
  joinByLink: (sessionId: string, nickname: string) =>
    request<Identity>(`/api/rooms/${sessionId}/visitors`, { method: "POST", body: JSON.stringify({ nickname }) }),
  getRoom: (sessionId: string, visitorId: string) =>
    request<Room>(`/api/rooms/${sessionId}?visitor_id=${encodeURIComponent(visitorId)}`),
  getMessages: (sessionId: string, visitorId: string) =>
    request<Message[]>(`/api/rooms/${sessionId}/messages?visitor_id=${encodeURIComponent(visitorId)}`),
  getSummary: (sessionId: string, visitorId: string) =>
    request<{ latest: SummaryVersion | null; history: SummaryVersion[] }>(`/api/rooms/${sessionId}/summary?visitor_id=${encodeURIComponent(visitorId)}`),
  getIdeas: (sessionId: string, visitorId: string) =>
    request<{ ideas: Idea[] }>(`/api/rooms/${sessionId}/ideas?visitor_id=${encodeURIComponent(visitorId)}`),
  getTodos: (sessionId: string, visitorId: string) =>
    request<{ todos: Todo[] }>(`/api/rooms/${sessionId}/todos?visitor_id=${encodeURIComponent(visitorId)}`),
  updateTodo: (sessionId: string, visitorId: string, todo: Pick<Todo, "id" | "title" | "assignee" | "status" | "due_at">) =>
    request<{ todo: Todo }>(`/api/rooms/${sessionId}/todos/${todo.id}`, {
      method: "PUT",
      body: JSON.stringify({ visitor_id: visitorId, title: todo.title, assignee: todo.assignee, status: todo.status, due_at: todo.due_at }),
    }),
  getUsage: (sessionId: string, visitorId: string) =>
    request<RoomUsage>(`/api/rooms/${sessionId}/usage?visitor_id=${encodeURIComponent(visitorId)}`),
  reviewFinish: (sessionId: string, identity: Identity) =>
    request<FinishReview>(`/api/rooms/${sessionId}/finish/review`, {
      method: "POST", body: JSON.stringify({ visitor_id: identity.visitor_id, owner_token: identity.owner_token }),
    }),
  finishRoom: (sessionId: string, identity: Identity, missing_dimensions: string[]) =>
    request<RoomReport>(`/api/rooms/${sessionId}/finish`, {
      method: "POST", body: JSON.stringify({ visitor_id: identity.visitor_id, owner_token: identity.owner_token, missing_dimensions }),
    }),
  getRoomReport: (sessionId: string, visitorId: string) =>
    request<RoomReport>(`/api/rooms/${sessionId}/report?visitor_id=${encodeURIComponent(visitorId)}`),
  roomReportExportUrl: (sessionId: string, visitorId: string, format: "md" | "docx") =>
    `${API_BASE}/api/rooms/${sessionId}/report/export.${format}?visitor_id=${encodeURIComponent(visitorId)}`,
  getVotes: (sessionId: string, visitorId: string) =>
    request<{ votes: import("../components/VoteCard").Vote[] }>(`/api/rooms/${sessionId}/votes?visitor_id=${encodeURIComponent(visitorId)}`),
  castVote: (sessionId: string, voteId: number, visitorId: string, optionId: number) =>
    request<{ vote: import("../components/VoteCard").Vote }>(`/api/rooms/${sessionId}/votes/${voteId}/records`, {
      method: "POST", body: JSON.stringify({ visitor_id: visitorId, option_id: optionId }),
    }),
  closeVote: (sessionId: string, voteId: number, identity: Identity) =>
    request<{ vote: import("../components/VoteCard").Vote }>(`/api/rooms/${sessionId}/votes/${voteId}/close`, {
      method: "POST", body: JSON.stringify({ visitor_id: identity.visitor_id, owner_token: identity.owner_token }),
    }),
  getReplay: (sessionId: string, visitorId: string) =>
    request<{ topic: string; events: ReplayEvent[] }>(`/api/rooms/${sessionId}/replay?visitor_id=${encodeURIComponent(visitorId)}`),
  getMentions: (sessionId: string, visitorId: string) =>
    request<{ message_ids: number[] }>(`/api/rooms/${sessionId}/mentions?visitor_id=${encodeURIComponent(visitorId)}`),
  markMentionsRead: (sessionId: string, identity: Identity, message_ids: number[]) =>
    request<{ ok: boolean }>(`/api/rooms/${sessionId}/mentions/read`, {
      method: "POST",
      body: JSON.stringify({ visitor_id: identity.visitor_id, message_ids }),
    }),
  sendMessage: (sessionId: string, payload: { visitor_id: string; content: string; reply_to_message_id?: number; image_url?: string }) =>
    request<Message>(`/api/rooms/${sessionId}/messages`, { method: "POST", body: JSON.stringify(payload) }),
  uploadImage: async (sessionId: string, visitorId: string, file: File) => {
    const form = new FormData();
    form.append("visitor_id", visitorId);
    form.append("image", file);
    return request<{ image_url: string }>(`/api/rooms/${sessionId}/upload`, { method: "POST", body: form });
  },
  recallMessage: (sessionId: string, messageId: number, visitorId: string) =>
    request<Message>(`/api/rooms/${sessionId}/messages/${messageId}/recall?visitor_id=${encodeURIComponent(visitorId)}`, { method: "POST" }),
  setPhase: (sessionId: string, identity: Identity, phase: Room["phase"]) =>
    request<{ phase: Room["phase"] }>(`/api/rooms/${sessionId}/phase`, {
      method: "PUT",
      body: JSON.stringify({ visitor_id: identity.visitor_id, owner_token: identity.owner_token, phase }),
    }),
  setModel: (sessionId: string, identity: Identity, model: string) =>
    request<{ model: string }>(`/api/rooms/${sessionId}/model`, {
      method: "PUT",
      body: JSON.stringify({ visitor_id: identity.visitor_id, owner_token: identity.owner_token, model }),
    }),
  setRedTeam: (sessionId: string, identity: Identity, enabled: boolean) =>
    request<{ enabled: boolean }>(`/api/rooms/${sessionId}/red-team`, {
      method: "PUT", body: JSON.stringify({ visitor_id: identity.visitor_id, owner_token: identity.owner_token, enabled }),
    }),
  imageUrl: (path: string) => `${API_BASE}${path}`,
  socketUrl: (sessionId: string, visitorId: string) => {
    const url = new URL(`/ws/${sessionId}?visitor_id=${encodeURIComponent(visitorId)}`, API_BASE);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return url.toString();
  },
};

export function saveIdentity(identity: Identity) {
  localStorage.setItem(`roomai:identity:${identity.session_id}`, JSON.stringify(identity));
}

export function loadIdentity(sessionId: string): Identity | null {
  try {
    const raw = localStorage.getItem(`roomai:identity:${sessionId}`);
    return raw ? (JSON.parse(raw) as Identity) : null;
  } catch {
    return null;
  }
}

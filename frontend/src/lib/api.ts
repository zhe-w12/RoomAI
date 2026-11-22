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
};

export type SummaryContent = {
  consensus: string[];
  disagreements: string[];
  todos: { title: string; assignee?: string }[];
  dimension_coverage: Record<string, boolean>;
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
  getUsage: (sessionId: string, visitorId: string) =>
    request<RoomUsage>(`/api/rooms/${sessionId}/usage?visitor_id=${encodeURIComponent(visitorId)}`),
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

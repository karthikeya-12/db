/** Chat client: conversation CRUD plus a reader for the server-sent-event answer stream. */
import { ApiError, api } from "@/lib/api";

export interface ChatSource {
  n: number;
  title: string;
  item_id: string;
  source_id: string;
  snippet: string;
  cited: boolean;
}

export interface ChatMessage {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  sources: ChatSource[];
  error?: boolean;
  general?: boolean; // answered from general knowledge, without searching the archive
}

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
}

export type StreamEvent =
  | { type: "conversation"; id: string; title: string }
  | { type: "route"; route: "general" | "archive" }
  | { type: "sources"; sources: ChatSource[] }
  | { type: "token"; text: string }
  | { type: "done"; message_id: number; sources: ChatSource[] }
  | { type: "error"; message: string };

export interface IndexingStatus {
  total: number;
  indexed: number;
  pending: number;
  sources: { source_id: string; total: number; pending: number }[];
}

export const chatApi = {
  status: () => api.get<IndexingStatus>("/api/chat/status"),
  list: () => api.get<ConversationSummary[]>("/api/chat/conversations"),
  get: (id: string) => api.get<ConversationDetail>(`/api/chat/conversations/${id}`),
  remove: (id: string) => fetch(`/api/chat/conversations/${id}`, { method: "DELETE" }),
};

/** Parses one SSE block ("event: x\ndata: {...}") into a typed event. */
export function parseEvent(block: string): StreamEvent | null {
  let name = "";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!name || !data) return null;
  try {
    return { type: name, ...JSON.parse(data) } as StreamEvent;
  } catch {
    return null;
  }
}

export async function* streamAnswer(
  message: string,
  conversationId: string | null,
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (response.status === 401) {
    window.location.assign(`/login?next=${encodeURIComponent("/chat")}`);
    return;
  }
  if (!response.ok || !response.body) throw new ApiError(`Request failed (${response.status})`, response.status);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const event = parseEvent(buffer.slice(0, cut));
      buffer = buffer.slice(cut + 2);
      if (event) yield event;
    }
  }
}

export type Citation = {
  n: number;
  source: string;
  page: number | null;
  kind: string;
  snippet: string;
};

export type BookingCard = {
  type: "confirm_booking";
  action_id: string;
  restaurant: string;
  party_size: number;
  time: string;
  requested_by: string;
  status: "pending" | "confirmed" | "cancelled";
  decided_by?: string;
};

export type ChatMessage = {
  id: string;
  author: string;
  text: string;
  kind: "user" | "aria" | "system";
  ts: string;
  card?: BookingCard | null;
  citations?: Citation[] | null;
};

export type Status = "connecting" | "open" | "closed";

export type Settings = {
  provider: string;
  chat_model: string;
  embed_model: string;
  vision_model: string;
  retrieval_k: number;
  retrieval_min_score: number;
  proactive_enabled: boolean;
  proactive_every_n: number;
  proactive_cooldown_s: number;
};

export type ServerEvent =
  | { type: "history"; messages: ChatMessage[] }
  | { type: "message"; message: ChatMessage }
  | { type: "message_update"; message: ChatMessage }
  | { type: "presence"; users: string[] }
  | { type: "typing"; who: string; on: boolean }
  | { type: "settings"; settings: Settings };

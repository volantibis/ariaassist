import type { BookingCard, ChatMessage, Citation } from "../types";

function initials(name: string) {
  return name
    .split(/\s+/)
    .map((part) => part.charAt(0).toUpperCase())
    .slice(0, 2)
    .join("");
}

function formatTime(ts: string) {
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return ts;
  }
}

function Citations({ items }: { items: Citation[] }) {
  return (
    <div className="citations">
      {items.map((c) => (
        <span key={c.n} className="chip" title={c.snippet}>
          [{c.n}] {c.source}
          {c.page ? ` p.${c.page}` : ""}
          {c.kind === "image_caption" ? "  ·  image" : ""}
        </span>
      ))}
    </div>
  );
}

function ConfirmCard({
  card,
  onDecide,
}: {
  card: BookingCard;
  onDecide: (actionId: string, approve: boolean) => void;
}) {
  const pending = card.status === "pending";
  return (
    <div className="card">
      <div className="card-title">Table booking</div>
      <div className="card-body">
        {card.restaurant} &nbsp;·&nbsp; {card.party_size} people &nbsp;·&nbsp; {card.time}
      </div>
      {pending ? (
        <div className="card-actions">
          <button onClick={() => onDecide(card.action_id, true)}>Approve</button>
          <button className="secondary" onClick={() => onDecide(card.action_id, false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div className={`card-status ${card.status}`}>
          {card.status === "confirmed" ? "✓ Approved" : "✗ Cancelled"}
          {card.decided_by ? ` by ${card.decided_by}` : ""}
        </div>
      )}
    </div>
  );
}

export default function MessageItem({
  msg,
  me,
  hideAuthor,
  onDecide,
}: {
  msg: ChatMessage;
  me: string;
  hideAuthor?: boolean;
  onDecide: (actionId: string, approve: boolean) => void;
}) {
  if (msg.kind === "system") return <div className="system">{msg.text}</div>;
  const isMine = msg.author === me;
  const kindClass = msg.kind === "user" ? (isMine ? "mine" : "other") : msg.kind;
  return (
    <div className={`msg ${kindClass} ${isMine ? "mine" : ""}`}>
      {!hideAuthor && (
        <div className="msg-header">
          <div className="avatar">{initials(msg.author)}</div>
          <div>
            <span className="author">{msg.author}</span>
            <span className="timestamp">{` ${formatTime(msg.ts)}`}</span>
          </div>
        </div>
      )}
      <div className="bubble">{msg.text}</div>
      {msg.citations && msg.citations.length > 0 && <Citations items={msg.citations} />}
      {msg.card && <ConfirmCard card={msg.card} onDecide={onDecide} />}
    </div>
  );
}

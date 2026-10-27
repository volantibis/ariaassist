import type { BookingCard, ChatMessage, Citation } from "../types";

function Citations({ items }: { items: Citation[] }) {
  return (
    <div className="citations">
      {items.map((c) => (
        <span key={c.n} className="chip" title={c.snippet}>
          [{c.n}] {c.source}
          {c.page ? ` p.${c.page}` : ""}
          {c.kind === "image_caption" ? " (image)" : ""}
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
      <div>
        {card.restaurant} · {card.party_size} people · {card.time}
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
          {card.status === "confirmed" ? "Approved" : "Cancelled"}
          {card.decided_by ? ` by ${card.decided_by}` : ""}
        </div>
      )}
    </div>
  );
}

export default function MessageItem({
  msg,
  me,
  onDecide,
}: {
  msg: ChatMessage;
  me: string;
  onDecide: (actionId: string, approve: boolean) => void;
}) {
  if (msg.kind === "system") return <div className="system">{msg.text}</div>;
  return (
    <div className={`msg ${msg.kind} ${msg.author === me ? "mine" : ""}`}>
      <div className="author">{msg.author}</div>
      <div className="bubble">{msg.text}</div>
      {msg.citations && msg.citations.length > 0 && <Citations items={msg.citations} />}
      {msg.card && <ConfirmCard card={msg.card} onDecide={onDecide} />}
    </div>
  );
}

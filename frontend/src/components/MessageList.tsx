import { useEffect, useRef } from "react";
import type { ChatMessage } from "../types";
import MessageItem from "./MessageItem";

export default function MessageList({
  messages,
  typing,
  me,
  onDecide,
}: {
  messages: ChatMessage[];
  typing: string[];
  me: string;
  onDecide: (actionId: string, approve: boolean) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, typing]);

  return (
    <section className="messages">
      {messages.map((m) => (
        <MessageItem key={m.id} msg={m} me={me} onDecide={onDecide} />
      ))}
      {typing.length > 0 && <div className="typing">{typing.join(", ")} is typing…</div>}
      <div ref={bottomRef} />
    </section>
  );
}

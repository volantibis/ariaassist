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
  const containerRef = useRef<HTMLDivElement>(null);
  const lastLengthRef = useRef(0);

  // Jump to the latest message when the chat opens, and every time a new
  // message arrives in the room.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const grew = messages.length > lastLengthRef.current;
    lastLengthRef.current = messages.length;
    if (grew || messages.length === 0) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages]);

  return (
    <section className="messages" ref={containerRef}>
      {messages.length === 0 && (
        <div className="empty">
          No messages yet. Mention <b>@Aria</b> or attach a document to get started.
        </div>
      )}
      {messages.map((m, i) => {
        const prev = messages[i - 1];
        const hideAuthor = !!prev && prev.author === m.author;
        return (
          <MessageItem
            key={m.id}
            msg={m}
            me={me}
            hideAuthor={hideAuthor}
            onDecide={onDecide}
          />
        );
      })}
      {typing.length > 0 && (
        <div className="typing">
          {typing.join(", ")}{typing.length === 1 ? " is typing…" : " are typing…"}
          <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
        </div>
      )}
      <div ref={bottomRef} />
    </section>
  );
}

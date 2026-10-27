import { useCallback, useEffect, useRef, useState } from "react";
import { uploadFile, WS_URL } from "./api";
import type { ChatMessage, ServerEvent, Status } from "./types";

/** Owns the WebSocket and all chat state. Components just render what it returns. */
export function useChat(name: string, onLeave: (reason: string) => void) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [users, setUsers] = useState<string[]>([]);
  const [typing, setTyping] = useState<string[]>([]);
  const [status, setStatus] = useState<Status>("connecting");
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const onLeaveRef = useRef(onLeave);
  onLeaveRef.current = onLeave;

  useEffect(() => {
    let ws: WebSocket | null = null;
    // Deferred connect: React StrictMode mounts, unmounts and remounts effects in dev.
    // Without the delay the first socket would claim the name and the second be rejected.
    const timer = setTimeout(() => {
      ws = new WebSocket(`${WS_URL}?name=${encodeURIComponent(name)}`);
      wsRef.current = ws;
      ws.onopen = () => setStatus("open");
      ws.onclose = (e) => {
        setStatus("closed");
        if (e.code === 4409) onLeaveRef.current("That name is taken. Try another.");
        else if (e.code === 4400) onLeaveRef.current("Name must be 1-24 characters.");
      };
      ws.onmessage = (e) => {
        const ev: ServerEvent = JSON.parse(e.data);
        switch (ev.type) {
          case "history":
            return setMessages(ev.messages);
          case "message":
            return setMessages((m) => [...m, ev.message]);
          case "message_update":
            return setMessages((m) => m.map((x) => (x.id === ev.message.id ? ev.message : x)));
          case "presence":
            return setUsers(ev.users);
          case "typing":
            return setTyping((t) =>
              ev.on ? [...new Set([...t, ev.who])] : t.filter((x) => x !== ev.who),
            );
        }
      };
    }, 50);
    return () => {
      clearTimeout(timer);
      ws?.close();
    };
  }, [name]);

  const sendJson = useCallback((payload: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(payload));
  }, []);

  const send = useCallback((text: string) => sendJson({ type: "message", text }), [sendJson]);
  const confirm = useCallback(
    (actionId: string, approve: boolean) =>
      sendJson({ type: "confirm", action_id: actionId, approve }),
    [sendJson],
  );

  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      setNotice("");
      const error = await uploadFile(file, name);
      setUploading(false);
      if (error) setNotice(error);
    },
    [name],
  );

  return { messages, users, typing, status, uploading, notice, send, confirm, upload };
}

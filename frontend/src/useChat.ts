import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSettings, updateSettings, uploadFile, WS_URL } from "./api";
import type { ChatMessage, ServerEvent, Settings, Status, Upload } from "./types";

/** Backoff delays (ms) between reconnection attempts. */
const RECONNECT_DELAYS = [1000, 2000, 4000, 8000];

/** Owns the WebSocket and all chat state. Components just render what it returns. */
export function useChat(name: string, onLeave: (reason: string) => void) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [users, setUsers] = useState<string[]>([]);
  const [typing, setTyping] = useState<string[]>([]);
  const [status, setStatus] = useState<Status>("connecting");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [notice, setNotice] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const mountedRef = useRef(true);
  const onLeaveRef = useRef(onLeave);
  onLeaveRef.current = onLeave;

  const connect = () => {
    if (!mountedRef.current) return;
    const ws = new WebSocket(`${WS_URL}?name=${encodeURIComponent(name)}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("open");
      attemptRef.current = 0;
      void fetchSettings().then(setSettings);
    };
    ws.onclose = (e) => {
      setStatus("closed");
      if (e.code === 4409) {
        onLeaveRef.current("That name is taken. Try another.");
      } else if (e.code === 4400) {
        onLeaveRef.current("Name must be 1-24 characters.");
      } else if (mountedRef.current) {
        // Auto-reconnect with exponential backoff for unexpected drops.
        const delay = RECONNECT_DELAYS[Math.min(attemptRef.current, RECONNECT_DELAYS.length - 1)];
        attemptRef.current += 1;
        reconnectRef.current = setTimeout(connect, delay);
      }
    };
    ws.onmessage = (e) => {
      const ev: ServerEvent = JSON.parse(e.data);
      switch (ev.type) {
        case "history":
          return setMessages(ev.messages);
        case "message":
          return setMessages((m) =>
            m.some((x) => x.id === ev.message.id) ? m : [...m, ev.message],
          );
        case "message_update":
          return setMessages((m) => m.map((x) => (x.id === ev.message.id ? ev.message : x)));
        case "presence":
          return setUsers(ev.users);
        case "typing":
          return setTyping((t) =>
            ev.on ? [...new Set([...t, ev.who])] : t.filter((x) => x !== ev.who),
          );
        case "settings":
          return setSettings(ev.settings);
        case "upload_done":
          return setUploads((prev) => {
            const next = prev.map((u): Upload =>
              // Match by the client-generated id so a fast ingestion that
              // finishes before the UI registers the file still lands. Fall
              // back to the file name for older servers that don't echo the id.
              (u.id === ev.client_upload_id || u.name === ev.file) &&
              u.status !== "done"
                ? { ...u, status: "done" }
                : u,
            );
            const finished = next.find(
              (u) =>
                (u.id === ev.client_upload_id || u.name === ev.file) && u.status === "done",
            );
            if (finished) {
              // The "processing" chip must disappear as soon as processing
              // completes / the input is back active — remove it immediately.
              // A brief disappearing notification confirms completion.
              setNotice(`Uploaded ${finished.name}`);
              setTimeout(() => setNotice(() => ""), 3000);
              return next.filter((u) => u.id !== finished.id);
            }
            return next;
          });
        case "upload_error":
          setUploads((prev) =>
            prev.map((u): Upload =>
              (u.id === ev.client_upload_id || u.name === ev.file)
                ? { ...u, status: "error", reason: ev.reason }
                : u,
            ),
          );
          setNotice(`Upload of ${ev.file} failed: ${ev.reason}`);
          return;
      }
    };
  };

  useEffect(() => {
    mountedRef.current = true;
    // Deferred connect: React StrictMode mounts, unmounts and remounts effects in dev.
    // Without the delay the first socket would claim the name and the second be rejected.
    const timer = setTimeout(connect, 50);
    return () => {
      clearTimeout(timer);
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      mountedRef.current = false;
      wsRef.current?.close();
      wsRef.current = null;
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

  const setRemoteSettings = useCallback(
    async (patch: object): Promise<Settings | null> => {
      const updated = await updateSettings(patch);
      if (updated) setSettings(updated);
      return updated;
    },
    [],
  );

  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      setNotice("");
      // A client-generated id lets us match upload_done / upload_error to this
      // exact upload even if ingestion finishes before the UI registers it,
      // or when the server's safe-name'd file name differs from the original.
      const id = crypto.randomUUID();
      setUploads((prev) => [
        ...prev.filter((u) => u.status !== "done" && u.status !== "error"),
        { id, name: file.name, status: "uploading" },
      ]);
      const { name: serverName, error } = await uploadFile(file, name, id);
      const accepted = serverName ?? file.name;
      setUploading(false);
      setUploads((prev) =>
        prev.map((u): Upload =>
          u.id === id && u.status === "uploading"
            ? { ...u, name: accepted, status: "processing" }
            : u,
        ),
      );
      if (error) {
        setUploads((prev) =>
          prev.map((u): Upload =>
            u.id === id && u.status === "processing"
              ? { ...u, status: "error", reason: error }
              : u,
          ),
        );
        setNotice(`Upload of ${accepted} failed: ${error}`);
      }
    },
    [name],
  );

  return {
    messages,
    users,
    typing,
    status,
    uploading,
    uploads,
    notice,
    settings,
    send,
    confirm,
    setRemoteSettings,
    upload,
  };
}

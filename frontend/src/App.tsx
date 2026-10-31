import { FormEvent, useEffect, useState } from "react"
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";
import { useChat } from "./useChat";

function Join({ onJoin, error }: { onJoin: (name: string) => void; error: string }) {
  const [name, setName] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) onJoin(name.trim());
  };
  return (
    <main className="join">
      <form onSubmit={submit}>
        <h1>Aria</h1>
        <p>
          A group chat with an AI member. Mention <b>@Aria</b> to talk to her, or attach a
          document and ask about it.
        </p>
        <input
          autoFocus
          maxLength={24}
          placeholder="Pick a display name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button disabled={!name.trim()}>Join</button>
        {error && <p className="error">{error}</p>}
      </form>
    </main>
  );
}

function Chat({ name, onLeave }: { name: string; onLeave: (reason: string) => void }) {
  const chat = useChat(name, onLeave);
  const [dragOver, setDragOver] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);

  useEffect(() => {
    const over = (e: DragEvent) => { e.preventDefault(); setDragOver(true); };
    const leave = (e: DragEvent) => { e.preventDefault(); setDragOver(false); };
    const drop = (e: DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const f = e.dataTransfer?.files[0];
      if (f) chat.upload(f);
    };
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [chat.upload]);

  return (
    <div className="app">
      {dragOver && <div className="drag-overlay"><span>Drop a file to upload</span></div>}
      <header>
        <div className="app-title">Aria group chat</div>
        <span className={`status ${chat.status}`}>
          <span className="status-dot" />{chat.status}
        </span>
        <div className="header-right">
          <span className="users">{chat.users.join(", ")}</span>
          <button className="info-btn" aria-label="About Aria" onClick={() => setInfoOpen(true)}>
            i
          </button>
        </div>
      </header>
      {chat.status === "closed" && (
        <div className="health-banner">Models are down</div>
      )}
      {chat.notice && <div className="notice">{chat.notice}</div>}
      <MessageList
        messages={chat.messages}
        typing={chat.typing}
        me={name}
        onDecide={chat.confirm}
      />
      <Composer
        disabled={chat.status !== "open"}
        uploading={chat.uploading}
        onSend={chat.send}
        onUpload={chat.upload}
        onPaste={(files) => {
          for (const f of files) void chat.upload(f);
        }}
      />
      {infoOpen && (
        <div className="info-backdrop" onClick={() => setInfoOpen(false)}>
          <div className="info-popup" onClick={(e) => e.stopPropagation()}>
            <button className="info-close" onClick={() => setInfoOpen(false)}>✕</button>
            <h2>About Aria</h2>
            <ul>
              <li>Real-time group chat where an AI agent is a participant</li>
              <li>Proactive (time-gated intent detection) and reactive (@mention routing) via a
                LangGraph router
              </li>
              <li>Human-in-the-loop booking approval via LangGraph <code>interrupt()</code></li>
              <li>Multimodal RAG with page-level citations (PDF / text / image)</li>
              <li>Model-eval harness: local Ollama vs Azure AI Foundry on routing/retrieval accuracy</li>
              <li>Deployed on Azure: AI Foundry, AI Search, Key Vault, App Service</li>
            </ul>
            <p>
              Source: &nbsp;
              <a
                href="https://github.com/volantibis/ariaassist"
                target="_blank"
                rel="noopener noreferrer"
              >
                github.com/volantibis/ariaassist
              </a>
            </p>
            <p>
              Source: &nbsp;
              <a
                href="https://github.com/volantibis/ariaassist"
                target="_blank"
                rel="noopener noreferrer"
              >
                github.com/volantibis/ariaassist
              </a>
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [name, setName] = useState<string | null>(null);
  const [error, setError] = useState("");
  const leave = (reason: string) => {
    setName(null);
    setError(reason);
  };
  if (!name)
    return (
      <Join
        error={error}
        onJoin={(n) => {
          setError("");
          setName(n);
        }}
      />
    );
  return <Chat name={name} onLeave={leave} />;
}

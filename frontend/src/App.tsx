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
  useEffect(() => {
    const over = (e: DragEvent) => e.preventDefault();
    const drop = (e: DragEvent) => {
      e.preventDefault();
      const f = e.dataTransfer?.files[0];
      if (f) chat.upload(f);
    };
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, [chat.upload]);
  return (
    <div className="app">
      <header>
        <strong>Aria group chat</strong>
        <span className={`status ${chat.status}`}>{chat.status}</span>
        <span className="users">{chat.users.join(", ")}</span>
      </header>
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
        // onPaste={(e) => {
        //   const file = e.clipboardData.files[0];
        //   const text = e.clipboardData.getData("text");
        //   if (file) { e.preventDefault(); onUpload(file); }
        //   else if (text.length > 1500) {
        //     e.preventDefault();
        //     onUpload(new File([text], "pasted.md", { type: "text/markdown" }));
        //   }
        // }}
      />
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

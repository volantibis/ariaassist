import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";
import { useChat } from "./useChat";
import type { Settings } from "./types";

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

type Option = { value: string; label: string };

/** Dropdown options per tunable setting, matched to backend validation ranges. */
function fieldOptions(key: keyof SettingsForm): Option[] {
  switch (key) {
    case "retrieval_k":
      return [1, 2, 4, 6, 8, 10].map((n) => ({ value: String(n), label: `${n} chunks` }));
    case "retrieval_min_score":
      return [0.1, 0.25, 0.4, 0.6, 0.8].map((n) => ({ value: String(n), label: String(n) }));
    case "proactive_enabled":
      return [
        { value: "true", label: "On" },
        { value: "false", label: "Off" },
      ];
    case "proactive_every_n":
      return [1, 2, 4, 8].map((n) => ({ value: String(n), label: `${n} messages` }));
    case "proactive_cooldown_s":
      return [
        { value: "1", label: "1 second" },
        { value: "30", label: "30 seconds" },
        { value: "90", label: "90 seconds" },
        { value: "300", label: "5 minutes" },
      ];
  }
}

type SettingsForm = {
  retrieval_k: string;
  retrieval_min_score: string;
  proactive_enabled: string;
  proactive_every_n: string;
  proactive_cooldown_s: string;
};

const EMPTY_FORM: SettingsForm = {
  retrieval_k: "4",
  retrieval_min_score: "0.25",
  proactive_enabled: "true",
  proactive_every_n: "4",
  proactive_cooldown_s: "90",
};

function formFromSettings(s: Settings): SettingsForm {
  return {
    retrieval_k: String(s.retrieval_k),
    retrieval_min_score: String(s.retrieval_min_score),
    proactive_enabled: String(s.proactive_enabled),
    proactive_every_n: String(s.proactive_every_n),
    proactive_cooldown_s: String(s.proactive_cooldown_s),
  };
}

/** Only the fields that actually differ from the server values. */
function changedFields(form: SettingsForm, from: Settings | null): object {
  if (!from) return {};
  const typed = {
    retrieval_k: Number(form.retrieval_k),
    retrieval_min_score: Number(form.retrieval_min_score),
    proactive_enabled: form.proactive_enabled === "true",
    proactive_every_n: Number(form.proactive_every_n),
    proactive_cooldown_s: Number(form.proactive_cooldown_s),
  };
  const patch: Record<string, number | boolean> = {};
  if (typed.retrieval_k !== from.retrieval_k) patch["retrieval_k"] = typed.retrieval_k;
  if (typed.retrieval_min_score !== from.retrieval_min_score)
    patch["retrieval_min_score"] = typed.retrieval_min_score;
  if (typed.proactive_enabled !== from.proactive_enabled) patch["proactive_enabled"] = typed.proactive_enabled;
  if (typed.proactive_every_n !== from.proactive_every_n) patch["proactive_every_n"] = typed.proactive_every_n;
  if (typed.proactive_cooldown_s !== from.proactive_cooldown_s)
    patch["proactive_cooldown_s"] = typed.proactive_cooldown_s;
  return patch;
}

function rowLabel(key: keyof SettingsForm): string {
  switch (key) {
    case "retrieval_k":
      return "Retrieval K";
    case "retrieval_min_score":
      return "Min retrieval score";
    case "proactive_enabled":
      return "Proactive enabled";
    case "proactive_every_n":
      return "Proactive every N";
    case "proactive_cooldown_s":
      return "Proactive cooldown";
  }
}

function rowEnv(key: keyof SettingsForm): string {
  switch (key) {
    case "retrieval_k":
      return "RETRIEVAL_K";
    case "retrieval_min_score":
      return "RETRIEVAL_MIN_SCORE";
    case "proactive_enabled":
      return "PROACTIVE_ENABLED";
    case "proactive_every_n":
      return "PROACTIVE_EVERY_N";
    case "proactive_cooldown_s":
      return "PROACTIVE_COOLDOWN_S";
  }
}

function SettingsFormPanel({
  form,
  setField,
  patch,
  saving,
  saved,
  onSave,
}: {
  form: SettingsForm;
  setField: (key: keyof SettingsForm, value: string) => void;
  patch: object;
  saving: boolean;
  saved: boolean;
  onSave: () => void;
}) {
  return (
    <section className="settings-panel">
      {/* Models row across the top of the expanded panel. Sticky so it stays in
         view while scrolling; subtle divider, no card background. */}
      <section className="models-strip">
        <span className="models-icon">🤖</span>
        <span className="models-chip">qwen3:4b</span>
        <span className="models-chip">nomic-embed-text</span>
        <span className="models-chip">qwen3:4b</span>
      </section>

      <p className="models-note">Models are loaded by the backend</p>

      <div className="settings-fields">
        {["retrieval_k", "retrieval_min_score", "proactive_enabled", "proactive_every_n", "proactive_cooldown_s"].map(
          (key) => (
            <div className="field row" key={key}>
              <label htmlFor={key}>
                {rowLabel(key as keyof SettingsForm)}
                <span className="row-env">({rowEnv(key as keyof SettingsForm)})</span>
              </label>
              <select
                id={key}
                value={form[key as keyof SettingsForm]}
                onChange={(e) => setField(key as keyof SettingsForm, e.target.value)}
              >
                {fieldOptions(key as keyof SettingsForm).map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          ),
        )}
      </div>

      {Object.keys(patch).length > 0 ? (
        <p className="settings-unsaved">
          {Object.keys(patch).length} setting(s) changed - not saved yet.
        </p>
      ) : null}
      <div className="settings-save-wrap">
        <button className="settings-save" disabled={saving || Object.keys(patch).length === 0} onClick={onSave}>
          {saving ? "Saving…" : "Save settings"}
        </button>
      </div>
      {saved && <p className="settings-saved">Saved on the server.</p>}
    </section>
  );
}

function Chat({ name, onLeave }: { name: string; onLeave: (reason: string) => void }) {
  const chat = useChat(name, onLeave);
  const [dragOver, setDragOver] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [form, setForm] = useState<SettingsForm>(() => ({ ...EMPTY_FORM }));
  const settingsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (chat.settings) setForm(formFromSettings(chat.settings));
  }, [chat.settings]);

  // Close the settings panel when clicking outside of it (toggle + popup).
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (!settingsOpen || !settingsRef.current) return;
      if (settingsRef.current.contains(e.target as Node)) return;
      setSettingsOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [settingsOpen]);

  const setField = useCallback(
    (key: keyof SettingsForm, value: string) => setForm((f) => ({ ...f, [key]: value })),
    [],
  );

  const patch = useMemo(() => changedFields(form, chat.settings), [form, chat.settings]);

  const saveSettings = useCallback(async () => {
    setSaving(true);
    setSaved(false);
    const result = await chat.setRemoteSettings(patch);
    setSaving(false);
    setSaved(Boolean(result));
    setTimeout(() => setSaved(false), 2000);
  }, [chat, patch]);

  useEffect(() => {
    const over = (e: DragEvent) => { e.preventDefault(); setDragOver(true); };
    const leave = (e: DragEvent) => { e.preventDefault(); setDragOver(false); };
    const drop = (e: DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      for (const f of files) chat.upload(f);
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
        </div>
      </header>

      {/* Settings toggle blends into the header (right-aligned on desktop). Expanding slides out
         the settings panel to its right below the toggle. */}
      <section className={`settings-accordion ${settingsOpen ? "open" : ""}`} ref={settingsRef}>
        <div className="settings-column">
          <button className="settings-toggle" aria-expanded={settingsOpen} onClick={(e) => { e.stopPropagation(); setSettingsOpen((o) => !o); }}>
            <span className="toggle-label">agent settings</span>
            <span className="toggle-chevron">{settingsOpen ? "▲" : "▼"}</span>
          </button>
          {settingsOpen && (
            <SettingsFormPanel
              form={form}
              setField={setField}
              patch={patch}
              saving={saving}
              saved={saved}
              onSave={saveSettings}
            />
          )}
        </div>
      </section>

      {chat.status === "closed" && <div className="health-banner">Models are down</div>}
      {chat.notice && <div className="notice">{chat.notice}</div>}
      {chat.uploads.length > 0 && (
        <div className="uploads">
          {chat.uploads.map((u) => {
            let body = "";
            switch (u.status) {
              case "uploading":
                body = `Uploading ${u.name}…`;
                break;
              case "processing":
                body = `Processing ${u.name}…`;
                break;
              case "done":
                body = `Uploaded ${u.name}`;
                break;
              case "error":
                body = `⚠ ${u.name}: ${u.reason ?? "failed"}`;
                break;
            }
            return <div key={u.id} className={`upload upload--${u.status}`}>{body}</div>;
          })}
        </div>
      )}
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

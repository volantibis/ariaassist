import { FormEvent, useRef, useState } from "react";
import { ACCEPTED_FILES } from "../api";

export default function Composer({
  disabled,
  uploading,
  onSend,
  onUpload,
}: {
  disabled: boolean;
  uploading: boolean;
  onSend: (text: string) => void;
  onUpload: (file: File) => void;
}) {
  const [draft, setDraft] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || disabled) return;
    onSend(text);
    setDraft("");
  };

  return (
    <form className="composer" onSubmit={submit}>
      <input
        ref={fileRef}
        type="file"
        hidden
        accept={ACCEPTED_FILES}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onUpload(f);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        className="secondary"
        disabled={disabled || uploading}
        title="Upload a PDF, text file or image"
        onClick={() => fileRef.current?.click()}
      >
        {uploading ? "Uploading…" : "Attach"}
      </button>
      <input
        autoFocus
        placeholder="Message the group. Type @Aria to ask her something."
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <button disabled={disabled || !draft.trim()}>Send</button>
    </form>
  );
}

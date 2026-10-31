import {
  ClipboardEvent,
  FormEvent,
  KeyboardEvent,
  useCallback,
  useRef,
  useState,
} from "react";
import { ACCEPTED_FILES } from "../api";

const MAX_LENGTH = 1000;

export default function Composer({
  disabled,
  uploading,
  onSend,
  onUpload,
  onPaste,
}: {
  disabled: boolean;
  uploading: boolean;
  onSend: (text: string) => void;
  onUpload: (file: File) => void;
  onPaste?: (files: File[]) => void;
}) {
  const handlePaste = (e: ClipboardEvent<HTMLFormElement>) => {
    const files = Array.from(e.clipboardData.files);
    if (files.length && onPaste) {
      e.preventDefault();
      onPaste(files);
    }
  };
  const [draft, setDraft] = useState("");
  const [multiline, setMultiline] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const grow = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
    setMultiline(el.scrollHeight > el.clientHeight);
  }, []);

  const sendDraft = (text: string) => {
    if (!text || disabled) return;
    onSend(text);
    setDraft("");
    textareaRef.current?.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    sendDraft(draft.trim());
  };

  const handleUpload = (f: File) => {
    if (disabled || uploading) return;
    onUpload(f);
  };

  // On bigger devices plain Return sends the message; Shift+Return always
  // inserts a newline. On small devices Return makes a newline and the send
  // button does the sending.
  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && window.innerWidth > 600) {
      e.preventDefault();
      sendDraft(draft.trim());
    }
  };

  return (
    <form className="composer" onSubmit={submit} onPaste={onPaste ? handlePaste : undefined}>
      <div className="text-box">
        <input
          ref={fileRef}
          type="file"
          hidden
          accept={ACCEPTED_FILES}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleUpload(f);
            e.target.value = "";
          }}
        />
        <textarea
          ref={textareaRef}
          autoFocus
          placeholder="chat or type @Aria to ask"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={grow}
          maxLength={MAX_LENGTH}
          disabled={disabled || uploading}
          rows={1}
        />
        {multiline && <span className="char-count">{draft.length}/{MAX_LENGTH}</span>}
      </div>
      <button
        type="button"
        className="attach"
        disabled={disabled || uploading}
        title="Upload a PDF, text file or image"
        aria-label="Attach a file"
        onClick={() => fileRef.current?.click()}
      >
        📎
      </button>
      <button
        type="submit"
        className="send"
        disabled={disabled || !draft.trim()}
        title="Send"
      >
        →
      </button>
    </form>
  );
}

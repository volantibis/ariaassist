export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
export const WS_URL: string = import.meta.env.VITE_WS_URL ?? API_URL.replace(/^http/, "ws") + "/ws";

export const ACCEPTED_FILES = ".pdf,.txt,.md,.png,.jpg,.jpeg,.webp";

/** Upload a document or image. Resolves to an error message, or null on success. */
export async function uploadFile(file: File, uploader: string): Promise<string | null> {
  const form = new FormData();
  form.append("file", file);
  form.append("uploader", uploader);
  try {
    const res = await fetch(`${API_URL}/api/upload`, { method: "POST", body: form });
    if (res.ok) return null;
    const body = await res.json().catch(() => ({}));
    return body.detail ?? `Upload failed (${res.status})`;
  } catch {
    return "Could not reach the server.";
  }
}

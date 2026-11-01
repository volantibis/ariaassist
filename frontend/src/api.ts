export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";
import type { Settings } from "./types";

export const WS_URL: string = import.meta.env.VITE_WS_URL ?? API_URL.replace(/^http/, "ws") + "/ws";

export const ACCEPTED_FILES = ".pdf,.txt,.md,.png,.jpg,.jpeg,.webp";

/** Current settings as seen by the server (models + tunable behaviour). */
export async function fetchSettings(): Promise<Settings | null> {
  try {
    const res = await fetch(`${API_URL}/api/settings`, { signal: AbortSignal.timeout(3000) });
    if (!res.ok) return null;
    return (await res.json()) as Settings;
  } catch {
    return null;
  }
}

/** Partially update tunable settings on the server. Returns the new settings, or null. */
export async function updateSettings(patch: object): Promise<Settings | null> {
  try {
    const res = await fetch(`${API_URL}/api/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) return null;
    return (await res.json()) as Settings;
  } catch {
    return null;
  }
}

/** Returns true when the backend /health endpoint reports OK. */
export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/health`, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch {
    return false;
  }
}

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

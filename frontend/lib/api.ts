function errorText(body: unknown, fallback: string) {
  if (typeof body === "object" && body && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item: { msg?: string }) => item.msg || "Invalid input").join(". ");
  }
  return fallback;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...init, headers: { ...(init?.body && typeof init.body === "string" ? { "Content-Type": "application/json" } : {}), ...init?.headers }, cache: "no-store" });
  } catch {
    throw new Error("Cannot reach the local processing service. Start the backend, then try again.");
  }
  if (!response.ok) {
    let body: unknown;
    try { body = await response.json(); } catch { /* proxy may return plain text */ }
    throw new Error(errorText(body, response.status >= 500 ? "The local processing service is unavailable. Check the backend and retry." : `Request failed (${response.status}).`));
  }
  return response.json();
}

export function upload<T>(path: string, file: File, onProgress: (percent: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api${path}`);
    request.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)); };
    request.onerror = () => reject(new Error("Upload interrupted. Check that the local backend is running, then attach the source again."));
    request.onload = () => {
      let body: unknown;
      try { body = JSON.parse(request.responseText); } catch { reject(new Error("The backend returned an unexpected response. Please retry the upload.")); return; }
      if (request.status >= 200 && request.status < 300) resolve(body as T);
      else reject(new Error(errorText(body, `Upload failed (${request.status}).`)));
    };
    const data = new FormData(); data.append("file", file); request.send(data);
  });
}

export function timecode(value: number, precise = false): string {
  if (!Number.isFinite(value)) return "00:00";
  const ticks = precise ? Math.round(Math.max(0, value) * 100) : Math.floor(Math.max(0, value)) * 100;
  const hours = Math.floor(ticks / 360000); const minutes = Math.floor(ticks / 6000) % 60;
  const rest = precise ? `${String(Math.floor(ticks / 100) % 60).padStart(2, "0")}.${String(ticks % 100).padStart(2, "0")}` : String(Math.floor(ticks / 100) % 60).padStart(2, "0");
  return `${hours ? `${String(hours).padStart(2, "0")}:` : ""}${String(minutes).padStart(2, "0")}:${rest}`;
}

export function parseTime(value: string): number {
  if (!/^(?:\d+:){0,2}\d+(?:\.\d+)?$/.test(value.trim())) return NaN;
  return value.trim().split(":").reduce((total, part) => total * 60 + Number(part), 0);
}

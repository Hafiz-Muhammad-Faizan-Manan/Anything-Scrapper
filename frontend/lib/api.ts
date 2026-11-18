import type { JobResponse, RenderMode, ScrapeCreated } from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/+$/, "");

/** Turn a backend-relative path ("/api/image/…") into an absolute URL. */
export function apiUrl(path: string): string {
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map((d: { msg?: string }) => d.msg ?? "Invalid request").join("; ");
    }
  } catch {
    /* non-JSON body */
  }
  return `Request failed (HTTP ${res.status}).`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { ...init, cache: "no-store" });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(`Cannot reach the ImageExtractor API at ${API_BASE}. Is the backend running?`, 0);
  }
  if (!res.ok) throw new ApiError(await errorMessage(res), res.status);
  return (await res.json()) as T;
}

export function createScrapeJob(url: string, renderMode: RenderMode, signal?: AbortSignal): Promise<ScrapeCreated> {
  return request<ScrapeCreated>("/api/scrape", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, render_mode: renderMode }),
    signal,
  });
}

export function getScrapeJob(jobId: string, signal?: AbortSignal): Promise<JobResponse> {
  return request<JobResponse>(`/api/scrape/${encodeURIComponent(jobId)}`, { signal });
}

export async function checkHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(apiUrl("/api/health"), { signal, cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
    } catch {
      /* fall through */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : null;
}

export interface DownloadOptions {
  fallbackName: string;
  /** Called with a 0..1 fraction (or null when the size is unknown). */
  onProgress?: (fraction: number | null, receivedBytes: number) => void;
  /** Size estimate used when the server does not send Content-Length (streamed ZIPs). */
  expectedBytes?: number;
  signal?: AbortSignal;
}

/**
 * Download a backend file via fetch so failures can be reported in the UI (a plain link would
 * navigate to a JSON error page), then hand it to the browser as a file save.
 */
export type DownloadOutcome = "saved" | "native";

/**
 * Download a backend file.
 *
 * The primary path uses fetch so HTTP errors (expired job, ZIP failure) can be shown in the UI
 * and progress can be reported. If the transfer fails at the network level — e.g. security
 * software or a proxy interrupting script-initiated downloads of archives — it falls back to a
 * native browser download, which streams straight to disk. Returns which path was used.
 */
export async function downloadFile(path: string, opts: DownloadOptions): Promise<DownloadOutcome> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { signal: opts.signal, cache: "no-store" });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    nativeDownload(path);
    return "native";
  }
  if (!res.ok) throw new ApiError(await errorMessage(res), res.status);

  const filename = filenameFromDisposition(res.headers.get("content-disposition")) ?? opts.fallbackName;
  const length = Number(res.headers.get("content-length")) || 0;
  const expected = length || Number(res.headers.get("x-uncompressed-size")) || opts.expectedBytes || 0;

  let blob: Blob;
  try {
    if (res.body && opts.onProgress) {
      const reader = res.body.getReader();
      const chunks: BlobPart[] = [];
      let received = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.byteLength;
        opts.onProgress(expected ? Math.min(0.99, received / expected) : null, received);
      }
      blob = new Blob(chunks, { type: res.headers.get("content-type") ?? "application/octet-stream" });
      opts.onProgress(1, received);
    } else {
      blob = await res.blob();
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    nativeDownload(path);
    return "native";
  }
  saveBlob(blob, filename);
  return "saved";
}

/** Let the browser download the URL itself (relies on the server's Content-Disposition: attachment). */
export function nativeDownload(path: string): void {
  const a = document.createElement("a");
  a.href = apiUrl(path);
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

"use client";

import { useEffect, useRef, useState } from "react";

import { API_BASE, checkHealth } from "@/lib/api";
import { hostOf } from "@/lib/format";
import type { RenderMode } from "@/lib/types";
import { useScrapeJob } from "@/lib/useScrapeJob";

import { Gallery } from "./Gallery";
import { AlertIcon, ImageIcon, InfoIcon } from "./Icons";
import { JobProgress } from "./JobProgress";
import { useToast } from "./ToastProvider";
import { UrlForm } from "./UrlForm";

type Health = "checking" | "online" | "offline";

const FEATURES = [
  {
    title: "Every image source",
    body: "img, srcset, lazy-load attributes, <picture>, CSS backgrounds, Open Graph & Twitter cards and JSON-LD.",
  },
  {
    title: "JavaScript-aware",
    body: "Static HTML first for speed; headless Chrome renders and scrolls JS apps only when needed.",
  },
  {
    title: "Validated & de-duplicated",
    body: "Every file is downloaded once, verified by content, de-duplicated and sized before you see it.",
  },
];

export function ImageExtractorApp() {
  const { job, running, error, start, reset } = useScrapeJob();
  const { notify } = useToast();
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);
  const [health, setHealth] = useState<Health>("checking");
  const notified = useRef<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    checkHealth(ctrl.signal).then((ok) => {
      if (!ctrl.signal.aborted) setHealth(ok ? "online" : "offline");
    });
    return () => ctrl.abort();
  }, []);

  // Fire one notification per job when it reaches a terminal state.
  useEffect(() => {
    if (!job || (job.status !== "completed" && job.status !== "failed")) return;
    const key = `${job.job_id}:${job.status}`;
    if (notified.current === key) return;
    notified.current = key;
    if (job.status === "failed") {
      notify("error", "The website could not be scanned", job.error ?? undefined);
    } else if (job.image_count === 0) {
      notify("info", "No images found", "The page did not contain any downloadable images.");
    } else {
      notify(
        "success",
        "Scan complete",
        `${job.image_count} image${job.image_count === 1 ? "" : "s"} extracted from ${hostOf(job.final_url ?? job.url)}.`,
      );
    }
  }, [job, notify]);

  useEffect(() => {
    if (error) notify("error", "Scan could not be started", error);
  }, [error, notify]);

  async function handleScan(url: string, mode: RenderMode) {
    setPendingUrl(url);
    const jobId = await start(url, mode);
    if (jobId) {
      setHealth("online");
      notify("info", "Scan started", `Scanning ${hostOf(url)}${mode === "browser" ? " with headless Chrome" : ""}…`);
    }
  }

  const showProgress = running || (job && job.status === "failed");
  const completed = job?.status === "completed";

  return (
    <div className="relative flex min-h-full flex-1 flex-col">
      <div className="hero-glow pointer-events-none absolute inset-x-0 top-0 h-[480px]" aria-hidden />

      <header className="relative z-10 mx-auto flex w-full max-w-7xl items-center gap-3 px-4 py-5 sm:px-6">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-lg shadow-indigo-900/50">
          <ImageIcon />
        </span>
        <button type="button" onClick={reset} className="text-lg font-bold tracking-tight text-slate-50">
          Image<span className="text-indigo-400">Extractor</span>
        </button>
        <span
          className="ml-auto flex items-center gap-2 text-xs text-slate-400"
          title={`Backend: ${API_BASE}`}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              health === "online" ? "bg-emerald-400" : health === "offline" ? "bg-rose-500" : "bg-slate-500"
            }`}
          />
          <span className="hidden sm:inline">
            {health === "online" ? "API online" : health === "offline" ? "API offline" : "Connecting…"}
          </span>
        </span>
      </header>

      <main className="relative z-10 mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 pb-16 sm:px-6">
        <section className={`mx-auto w-full max-w-3xl text-center ${job || running ? "pt-2" : "pt-10 sm:pt-16"}`}>
          {!job && !running && (
            <>
              <h1 className="text-balance text-3xl font-bold tracking-tight text-slate-50 sm:text-5xl">
                Extract every image from a web page
              </h1>
              <p className="mx-auto mt-4 max-w-xl text-pretty text-slate-400">
                Paste a public URL. ImageExtractor finds, validates and previews every image on that page — download
                them one by one or all at once as a ZIP.
              </p>
            </>
          )}
          <div className="mt-8 text-left">
            <UrlForm busy={running} onSubmit={handleScan} />
          </div>
        </section>

        {health === "offline" && !running && !job && (
          <div className="mx-auto flex w-full max-w-3xl items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-200">
            <AlertIcon className="mt-0.5 shrink-0" />
            <p>
              The backend API is not reachable at <code className="font-mono">{API_BASE}</code>. Start it with{" "}
              <code className="font-mono">uvicorn app.main:app</code> or set <code>NEXT_PUBLIC_API_BASE_URL</code>.
            </p>
          </div>
        )}

        {error && !running && (
          <div className="mx-auto flex w-full max-w-3xl items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200">
            <AlertIcon className="mt-0.5 shrink-0" />
            <p>{error}</p>
          </div>
        )}

        {showProgress && (
          <div className="mx-auto w-full max-w-3xl">
            <JobProgress job={job} pendingUrl={pendingUrl} />
            {job?.status === "failed" && job.render_mode !== "browser" && job.error && (
              <p className="mt-3 px-1 text-sm text-slate-400">
                Tip: if this page relies on JavaScript, try the <strong className="text-slate-200">Browser</strong>{" "}
                rendering mode. Pages behind logins, paywalls or CAPTCHAs cannot be scanned.
              </p>
            )}
          </div>
        )}

        {job && job.warnings.length > 0 && (completed || job.status === "failed") && (
          <details className="mx-auto w-full max-w-7xl rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3 text-sm text-slate-400">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-slate-300">
              <InfoIcon width={16} height={16} className="text-sky-400" />
              {job.warnings.length} note{job.warnings.length === 1 ? "" : "s"} about this scan
              {job.used_browser && <span className="text-xs text-violet-300">· rendered with headless Chrome</span>}
            </summary>
            <ul className="mt-2 list-disc space-y-1 pl-6">
              {job.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </details>
        )}

        {completed && job.image_count > 0 && <Gallery job={job} />}

        {completed && job.image_count === 0 && (
          <div className="mx-auto flex w-full max-w-3xl flex-col items-center gap-3 rounded-2xl border border-dashed border-slate-700 p-10 text-center">
            <ImageIcon width={36} height={36} className="text-slate-600" />
            <h2 className="text-lg font-semibold text-slate-200">No images found</h2>
            <p className="max-w-md text-sm text-slate-400">
              {hostOf(job.final_url ?? job.url)} did not contain any downloadable images
              {job.used_browser ? ", even after rendering it in headless Chrome" : ""}.
              {job.render_mode !== "browser" && " Try the Browser rendering mode for JavaScript-heavy pages."}
            </p>
          </div>
        )}

        {!job && !running && (
          <section className="mx-auto mt-6 grid w-full max-w-5xl gap-4 sm:grid-cols-3" aria-label="Features">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded-2xl border border-slate-800 bg-slate-900/40 p-5">
                <h3 className="text-sm font-semibold text-slate-100">{f.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-400">{f.body}</p>
              </div>
            ))}
          </section>
        )}
      </main>

      <footer className="relative z-10 border-t border-slate-900 py-5 text-center text-xs text-slate-600">
        Scans a single public page only. Respect website terms and copyright when downloading images.
      </footer>
    </div>
  );
}

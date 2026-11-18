"use client";

import { hostOf } from "@/lib/format";
import type { JobResponse, JobStatus } from "@/lib/types";

import { AlertIcon, CheckIcon, Spinner } from "./Icons";

interface Step {
  key: JobStatus;
  label: string;
}

const STEPS: Step[] = [
  { key: "fetching", label: "Fetch" },
  { key: "rendering", label: "Render" },
  { key: "extracting", label: "Extract" },
  { key: "downloading", label: "Validate" },
  { key: "completed", label: "Done" },
];

const ORDER: JobStatus[] = ["queued", "fetching", "rendering", "extracting", "downloading", "completed"];

interface Props {
  job: JobResponse | null;
  pendingUrl: string | null;
}

export function JobProgress({ job, pendingUrl }: Props) {
  const status: JobStatus = job?.status ?? "queued";
  const failed = status === "failed";
  const percent = job?.progress.percent ?? 2;
  const currentIndex = ORDER.indexOf(status);
  const skipsRender = job ? !job.used_browser && currentIndex > ORDER.indexOf("rendering") : false;
  const staticOnly = job?.render_mode === "static";

  return (
    <section
      className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 shadow-lg shadow-black/20"
      aria-label="Scan progress"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            {failed ? (
              <AlertIcon className="text-rose-400" />
            ) : status === "completed" ? (
              <CheckIcon className="text-emerald-400" />
            ) : (
              <Spinner className="text-indigo-400" />
            )}
            {failed ? "Scan failed" : status === "completed" ? "Scan complete" : "Scanning"}
            <span className="truncate font-normal text-slate-400">
              {hostOf(job?.final_url ?? job?.url ?? pendingUrl ?? "")}
            </span>
          </p>
          <p className="mt-1 text-sm text-slate-400">{job?.error ?? job?.progress.message ?? "Creating scan job…"}</p>
        </div>
        <div className="flex items-center gap-2">
          {job?.used_browser && (
            <span className="rounded-full border border-violet-400/40 bg-violet-500/10 px-2.5 py-0.5 text-xs font-medium text-violet-200">
              Headless Chrome
            </span>
          )}
          {job && job.progress.found > 0 && (
            <span className="rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-0.5 text-xs text-slate-300">
              {job.progress.found} candidate{job.progress.found === 1 ? "" : "s"}
            </span>
          )}
        </div>
      </div>

      <div
        className="mt-4 h-2 overflow-hidden rounded-full bg-slate-800"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-500 ease-out ${
            failed ? "bg-rose-500" : status === "completed" ? "bg-emerald-500" : "progress-stripes bg-indigo-500"
          }`}
          style={{ width: `${Math.max(2, percent)}%` }}
        />
      </div>

      <ol className="mt-4 grid grid-cols-5 gap-2 text-center text-xs">
        {STEPS.map((step) => {
          const idx = ORDER.indexOf(step.key);
          const skipped = step.key === "rendering" && (skipsRender || (staticOnly && currentIndex > 1));
          const done = !failed && (currentIndex > idx || status === "completed");
          const active = !failed && status === step.key;
          return (
            <li key={step.key} className="flex flex-col items-center gap-1.5">
              <span
                className={`h-1.5 w-full rounded-full ${
                  skipped ? "bg-slate-800" : done ? "bg-emerald-500/70" : active ? "bg-indigo-400" : "bg-slate-800"
                }`}
              />
              <span
                className={
                  skipped
                    ? "text-slate-600 line-through"
                    : active
                      ? "font-medium text-indigo-200"
                      : done
                        ? "text-slate-300"
                        : "text-slate-500"
                }
              >
                {step.label}
              </span>
            </li>
          );
        })}
      </ol>

      {status === "downloading" && job && job.progress.total > 0 && (
        <p className="mt-3 text-xs text-slate-500">
          Validated {job.progress.processed} of {job.progress.total} image URLs
        </p>
      )}
    </section>
  );
}

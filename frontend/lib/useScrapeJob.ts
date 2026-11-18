"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, createScrapeJob, getScrapeJob } from "./api";
import { TERMINAL_STATUSES, type JobResponse, type RenderMode } from "./types";

const POLL_INTERVAL_MS = 800;
const MAX_POLL_FAILURES = 6;

export interface ScrapeJobState {
  job: JobResponse | null;
  /** True between clicking "Scan" and the job reaching a terminal state. */
  running: boolean;
  /** Error that prevented a job from being created or tracked (not a job failure). */
  error: string | null;
  start: (url: string, mode: RenderMode) => Promise<string | null>;
  reset: () => void;
}

/** Creates a scrape job and polls its status until it completes or fails. */
export function useScrapeJob(): ScrapeJobState {
  const [job, setJob] = useState<JobResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    runId.current += 1;
    controller.current?.abort();
    controller.current = null;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  const start = useCallback(
    async (url: string, mode: RenderMode): Promise<string | null> => {
      stop();
      const id = runId.current;
      const ctrl = new AbortController();
      controller.current = ctrl;

      function poll(jobId: string, failures = 0): void {
        getScrapeJob(jobId, ctrl.signal)
          .then((data) => {
            if (id !== runId.current) return;
            setJob(data);
            if (TERMINAL_STATUSES.has(data.status)) {
              setRunning(false);
              return;
            }
            timer.current = setTimeout(() => poll(jobId), POLL_INTERVAL_MS);
          })
          .catch((err: unknown) => {
            if (id !== runId.current || (err as Error).name === "AbortError") return;
            if (err instanceof ApiError && err.status === 404) {
              setError("The scan job expired or the backend was restarted. Please scan again.");
              setRunning(false);
              return;
            }
            if (failures + 1 >= MAX_POLL_FAILURES) {
              setError(err instanceof Error ? err.message : "Lost connection to the backend.");
              setRunning(false);
              return;
            }
            const delay = POLL_INTERVAL_MS * 2 ** Math.min(failures + 1, 4);
            timer.current = setTimeout(() => poll(jobId, failures + 1), delay);
          });
      }

      setJob(null);
      setError(null);
      setRunning(true);
      try {
        const created = await createScrapeJob(url, mode, ctrl.signal);
        if (id !== runId.current) return null;
        poll(created.job_id);
        return created.job_id;
      } catch (err) {
        if (id !== runId.current || (err as Error).name === "AbortError") return null;
        setError(err instanceof Error ? err.message : "Could not start the scan.");
        setRunning(false);
        return null;
      }
    },
    [stop],
  );

  const reset = useCallback(() => {
    stop();
    setJob(null);
    setError(null);
    setRunning(false);
  }, [stop]);

  return { job, running, error, start, reset };
}

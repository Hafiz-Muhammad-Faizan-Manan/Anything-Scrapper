"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import { downloadFile } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import type { ImageInfo, JobResponse } from "@/lib/types";

import { ImageCard } from "./ImageCard";
import { ArchiveIcon, SearchIcon, Spinner } from "./Icons";
import { Lightbox } from "./Lightbox";
import { useToast } from "./ToastProvider";

interface Props {
  job: JobResponse;
}

export function Gallery({ job }: Props) {
  const { notify } = useToast();
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [downloading, setDownloading] = useState<ReadonlySet<string>>(new Set());
  const [zipProgress, setZipProgress] = useState<number | null | undefined>(undefined); // undefined = idle
  const previewErrors = useRef(0);
  const previewErrorTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const types = useMemo(() => {
    const counts = new Map<string, number>();
    for (const img of job.images) counts.set(img.extension, (counts.get(img.extension) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [job.images]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return job.images.filter(
      (img) =>
        (typeFilter === "all" || img.extension === typeFilter) &&
        (!q || img.filename.toLowerCase().includes(q) || img.url.toLowerCase().includes(q)),
    );
  }, [job.images, typeFilter, query]);

  const downloadOne = useCallback(
    async (image: ImageInfo) => {
      setDownloading((s) => new Set(s).add(image.id));
      try {
        await downloadFile(image.download_url, { fallbackName: image.filename });
      } catch (err) {
        notify("error", `Could not download ${image.filename}`, (err as Error).message);
      } finally {
        setDownloading((s) => {
          const next = new Set(s);
          next.delete(image.id);
          return next;
        });
      }
    },
    [notify],
  );

  const downloadAll = useCallback(async () => {
    if (!job.zip_url) return;
    setZipProgress(0);
    try {
      const outcome = await downloadFile(job.zip_url, {
        fallbackName: `imageextractor-${job.job_id}.zip`,
        expectedBytes: job.total_bytes,
        onProgress: (fraction) => setZipProgress(fraction),
      });
      if (outcome === "native") {
        notify("info", "ZIP download handed to the browser", "Check your browser's downloads for the archive.");
      } else {
        notify("success", "ZIP archive ready", `${job.image_count} images (${formatBytes(job.total_bytes)}) downloaded.`);
      }
    } catch (err) {
      notify("error", "ZIP generation failed", (err as Error).message);
    } finally {
      setZipProgress(undefined);
    }
  }, [job, notify]);

  const onPreviewError = useCallback(() => {
    // Aggregate preview failures into a single notification instead of one toast per image.
    previewErrors.current += 1;
    if (previewErrorTimer.current) clearTimeout(previewErrorTimer.current);
    previewErrorTimer.current = setTimeout(() => {
      const n = previewErrors.current;
      previewErrors.current = 0;
      notify(
        "error",
        n === 1 ? "An image preview failed to load" : `${n} image previews failed to load`,
        "The cached file may have expired. Try scanning again.",
      );
    }, 600);
  }, [notify]);

  const openImage = useCallback(
    (image: ImageInfo) => setLightbox(visible.findIndex((i) => i.id === image.id)),
    [visible],
  );
  const closeLightbox = useCallback(() => setLightbox(null), []);

  const zipBusy = zipProgress !== undefined;

  return (
    <section aria-label="Extracted images" className="flex flex-col gap-4">
      <div className="flex flex-col gap-4 rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:flex-row sm:items-center">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold text-slate-50">
            {job.image_count} image{job.image_count === 1 ? "" : "s"} extracted
          </h2>
          <p className="truncate text-sm text-slate-400" title={job.final_url ?? job.url}>
            {formatBytes(job.total_bytes)} total
            {job.page_title ? ` · ${job.page_title}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={downloadAll}
          disabled={zipBusy || !job.zip_url}
          className="relative inline-flex h-11 items-center justify-center gap-2 overflow-hidden rounded-xl bg-emerald-500 px-5 text-sm font-semibold text-emerald-950 shadow-lg shadow-emerald-900/30 transition hover:bg-emerald-400 disabled:cursor-wait disabled:opacity-80"
        >
          {zipBusy && (
            <span
              className="absolute inset-y-0 left-0 bg-emerald-300/60 transition-[width] duration-200"
              style={{ width: `${Math.round((zipProgress ?? 0.15) * 100)}%` }}
              aria-hidden
            />
          )}
          <span className="relative inline-flex items-center gap-2">
            {zipBusy ? <Spinner /> : <ArchiveIcon />}
            {zipBusy
              ? zipProgress == null
                ? "Preparing ZIP…"
                : `Downloading ${Math.round(zipProgress * 100)}%`
              : "Download All (.zip)"}
          </span>
        </button>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by type">
          {[["all", job.images.length] as [string, number], ...types].map(([type, count]) => (
            <button
              key={type}
              type="button"
              onClick={() => setTypeFilter(type)}
              aria-pressed={typeFilter === type}
              className={`rounded-full border px-3 py-1 text-xs font-medium uppercase tracking-wide transition ${
                typeFilter === type
                  ? "border-indigo-400/60 bg-indigo-500/15 text-indigo-200"
                  : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
              }`}
            >
              {type} <span className="text-slate-500">{count}</span>
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 sm:ml-auto sm:w-64">
          <SearchIcon width={16} height={16} className="text-slate-500" />
          <span className="sr-only">Filter by filename</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by name…"
            className="h-9 w-full bg-transparent text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none"
          />
        </label>
      </div>

      {visible.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-800 p-8 text-center text-sm text-slate-500">
          No images match the current filter.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 min-[420px]:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
          {visible.map((img) => (
            <ImageCard
              key={img.id}
              image={img}
              downloading={downloading.has(img.id)}
              onOpen={openImage}
              onDownload={downloadOne}
              onPreviewError={onPreviewError}
            />
          ))}
        </div>
      )}

      {lightbox !== null && lightbox >= 0 && (
        <Lightbox
          images={visible}
          index={lightbox}
          onClose={closeLightbox}
          onNavigate={setLightbox}
          onDownload={downloadOne}
        />
      )}
    </section>
  );
}

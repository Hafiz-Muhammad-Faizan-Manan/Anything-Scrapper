"use client";

import { useEffect } from "react";

import { apiUrl } from "@/lib/api";
import { formatBytes, formatDimensions } from "@/lib/format";
import type { ImageInfo } from "@/lib/types";

import { ChevronIcon, CloseIcon, DownloadIcon, ExternalIcon } from "./Icons";

interface Props {
  images: ImageInfo[];
  index: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
  onDownload: (image: ImageInfo) => void;
}

export function Lightbox({ images, index, onClose, onNavigate, onDownload }: Props) {
  const image = images[index];
  const hasPrev = index > 0;
  const hasNext = index < images.length - 1;

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && hasPrev) onNavigate(index - 1);
      else if (e.key === "ArrowRight" && hasNext) onNavigate(index + 1);
    }
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [index, hasPrev, hasNext, onClose, onNavigate]);

  if (!image) return null;
  const dims = formatDimensions(image.width, image.height);

  return (
    <div
      className="fixed inset-0 z-40 flex flex-col bg-slate-950/95 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview of ${image.filename}`}
      onClick={onClose}
    >
      <header className="flex items-center gap-3 px-4 py-3" onClick={(e) => e.stopPropagation()}>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-slate-100">{image.filename}</p>
          <p className="text-xs text-slate-400">
            {[dims, formatBytes(image.size_bytes), image.mime_type, `${index + 1} / ${images.length}`]
              .filter(Boolean)
              .join("  ·  ")}
          </p>
        </div>
        <button
          type="button"
          onClick={() => onDownload(image)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-400"
        >
          <DownloadIcon width={16} height={16} /> Download
        </button>
        <a
          href={image.url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="rounded-lg border border-slate-700 p-2 text-slate-300 hover:text-white"
          aria-label="Open original URL"
        >
          <ExternalIcon width={16} height={16} />
        </a>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-slate-700 p-2 text-slate-300 hover:text-white"
          aria-label="Close preview"
        >
          <CloseIcon width={16} height={16} />
        </button>
      </header>

      <div className="relative flex min-h-0 flex-1 items-center justify-center px-4 pb-6">
        {hasPrev && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onNavigate(index - 1);
            }}
            className="absolute left-3 z-10 rounded-full bg-slate-800/80 p-3 text-slate-200 hover:bg-slate-700"
            aria-label="Previous image"
          >
            <ChevronIcon direction="left" />
          </button>
        )}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          key={image.id}
          src={apiUrl(image.preview_url)}
          alt={image.filename}
          onClick={(e) => e.stopPropagation()}
          className="checkerboard max-h-full max-w-full rounded-lg object-contain shadow-2xl"
        />
        {hasNext && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onNavigate(index + 1);
            }}
            className="absolute right-3 z-10 rounded-full bg-slate-800/80 p-3 text-slate-200 hover:bg-slate-700"
            aria-label="Next image"
          >
            <ChevronIcon direction="right" />
          </button>
        )}
      </div>
    </div>
  );
}

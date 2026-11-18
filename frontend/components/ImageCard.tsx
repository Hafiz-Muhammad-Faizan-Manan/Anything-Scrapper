"use client";

import { memo, useState } from "react";

import { apiUrl } from "@/lib/api";
import { formatBytes, formatDimensions, SOURCE_LABELS } from "@/lib/format";
import type { ImageInfo } from "@/lib/types";

import { BrokenImageIcon, DownloadIcon, ExternalIcon, Spinner } from "./Icons";

interface Props {
  image: ImageInfo;
  downloading: boolean;
  onOpen: (image: ImageInfo) => void;
  onDownload: (image: ImageInfo) => void;
  onPreviewError: (image: ImageInfo) => void;
}

function ImageCardInner({ image, downloading, onOpen, onDownload, onPreviewError }: Props) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const dims = formatDimensions(image.width, image.height);

  return (
    <article className="group flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/70 shadow-md shadow-black/20 transition hover:-translate-y-0.5 hover:border-slate-600 hover:shadow-xl hover:shadow-indigo-950/40">
      <button
        type="button"
        onClick={() => onOpen(image)}
        className="checkerboard relative block aspect-[4/3] w-full overflow-hidden focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-400"
        aria-label={`Preview ${image.filename}`}
      >
        {!failed ? (
          // Previews are served by the API from its validated cache; next/image optimization is not needed.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={apiUrl(image.preview_url)}
            alt={image.filename}
            loading="lazy"
            decoding="async"
            onLoad={() => setLoaded(true)}
            onError={() => {
              setFailed(true);
              onPreviewError(image);
            }}
            className={`h-full w-full object-contain p-2 transition duration-300 group-hover:scale-[1.03] ${
              loaded ? "opacity-100" : "opacity-0"
            }`}
          />
        ) : (
          <span className="flex h-full w-full flex-col items-center justify-center gap-2 bg-slate-900 text-slate-500">
            <BrokenImageIcon width={28} height={28} />
            <span className="text-xs">Preview failed to load</span>
          </span>
        )}
        {!loaded && !failed && <span className="absolute inset-0 animate-pulse bg-slate-800/60" />}
        <span className="absolute left-2 top-2 rounded-md bg-slate-950/80 px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-slate-200 backdrop-blur">
          {image.extension}
        </span>
      </button>

      <div className="flex flex-1 flex-col gap-2 p-3">
        <p className="truncate text-sm font-medium text-slate-100" title={image.filename}>
          {image.filename}
        </p>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400">
          <span>{dims ?? "Unknown size"}</span>
          <span className="text-slate-600">•</span>
          <span>{formatBytes(image.size_bytes)}</span>
          <span className="ml-auto rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400" title="Where the image was found">
            {SOURCE_LABELS[image.source] ?? image.source}
          </span>
        </div>
        <div className="mt-auto flex gap-2 pt-1">
          <button
            type="button"
            onClick={() => onDownload(image)}
            disabled={downloading}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-slate-800 px-3 py-2 text-xs font-semibold text-slate-100 transition hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-60"
          >
            {downloading ? <Spinner className="h-4 w-4" /> : <DownloadIcon width={16} height={16} />}
            Download
          </button>
          <a
            href={image.url}
            target="_blank"
            rel="noopener noreferrer nofollow"
            className="inline-flex items-center justify-center rounded-lg border border-slate-700 px-2.5 text-slate-400 transition hover:border-slate-500 hover:text-slate-100"
            title="Open original URL"
            aria-label={`Open original URL of ${image.filename}`}
          >
            <ExternalIcon width={16} height={16} />
          </a>
        </div>
      </div>
    </article>
  );
}

export const ImageCard = memo(ImageCardInner);

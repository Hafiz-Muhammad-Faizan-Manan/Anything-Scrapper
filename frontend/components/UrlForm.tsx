"use client";

import { useState, type FormEvent } from "react";

import { normalizeInputUrl } from "@/lib/format";
import type { RenderMode } from "@/lib/types";

import { GlobeIcon, SearchIcon, Spinner } from "./Icons";

const MODES: { value: RenderMode; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "Fast static scan; headless Chrome only when the page needs JavaScript" },
  { value: "static", label: "Static", hint: "HTML only — fastest, never launches a browser" },
  { value: "browser", label: "Browser", hint: "Always render in headless Chrome (JS apps, lazy-loaded galleries)" },
];

interface Props {
  busy: boolean;
  onSubmit: (url: string, mode: RenderMode) => void;
}

export function UrlForm({ busy, onSubmit }: Props) {
  const [value, setValue] = useState("");
  const [mode, setMode] = useState<RenderMode>("auto");
  const [invalid, setInvalid] = useState(false);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const url = normalizeInputUrl(value);
    if (!url) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setValue(url);
    onSubmit(url, mode);
  }

  return (
    <form onSubmit={handleSubmit} className="w-full" noValidate>
      <div
        className={`flex flex-col gap-2 rounded-2xl border bg-slate-900/70 p-2 shadow-xl shadow-indigo-950/30 backdrop-blur transition focus-within:border-indigo-400/60 sm:flex-row ${
          invalid ? "border-rose-500/60" : "border-slate-700/70"
        }`}
      >
        <label className="flex min-w-0 flex-1 items-center gap-3 px-3">
          <GlobeIcon className="shrink-0 text-slate-500" />
          <span className="sr-only">Website URL</span>
          <input
            type="url"
            inputMode="url"
            autoComplete="url"
            spellCheck={false}
            placeholder="https://example.com/gallery"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              if (invalid) setInvalid(false);
            }}
            disabled={busy}
            className="h-12 w-full min-w-0 bg-transparent text-base text-slate-100 placeholder:text-slate-500 focus:outline-none disabled:opacity-60"
            aria-invalid={invalid}
            aria-describedby={invalid ? "url-error" : undefined}
          />
        </label>
        <button
          type="submit"
          disabled={busy || !value.trim()}
          className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-indigo-500 to-violet-500 px-6 text-sm font-semibold text-white shadow-lg shadow-indigo-900/40 transition hover:from-indigo-400 hover:to-violet-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? <Spinner /> : <SearchIcon />}
          {busy ? "Scanning…" : "Scan Website"}
        </button>
      </div>

      {invalid && (
        <p id="url-error" className="mt-2 px-2 text-sm text-rose-400">
          Please enter a valid public http(s) URL, e.g. https://example.com
        </p>
      )}

      <fieldset className="mt-4 flex flex-wrap items-center gap-2 px-1" disabled={busy}>
        <legend className="sr-only">Rendering mode</legend>
        <span className="mr-1 text-xs font-medium uppercase tracking-wider text-slate-500">Rendering</span>
        {MODES.map((m) => (
          <label
            key={m.value}
            title={m.hint}
            className={`cursor-pointer rounded-full border px-3 py-1 text-xs font-medium transition has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-indigo-400 ${
              mode === m.value
                ? "border-indigo-400/60 bg-indigo-500/15 text-indigo-200"
                : "border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"
            }`}
          >
            <input
              type="radio"
              name="render-mode"
              value={m.value}
              checked={mode === m.value}
              onChange={() => setMode(m.value)}
              className="sr-only"
            />
            {m.label}
          </label>
        ))}
        <span className="hidden text-xs text-slate-500 md:inline">{MODES.find((m) => m.value === mode)?.hint}</span>
      </fieldset>
    </form>
  );
}

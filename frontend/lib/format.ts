export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** i;
  return `${value >= 100 || i === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[i]}`;
}

export function formatDimensions(width: number | null, height: number | null): string | null {
  return width && height ? `${width} × ${height}` : null;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/** Accepts "example.com/page" and returns a normalized absolute URL, or null if it is not usable. */
export function normalizeInputUrl(raw: string): string | null {
  const value = raw.trim();
  if (!value) return null;
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`;
  try {
    const url = new URL(withScheme);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (!url.hostname || (!url.hostname.includes(".") && !url.hostname.includes(":"))) return null;
    return url.toString();
  } catch {
    return null;
  }
}

export const SOURCE_LABELS: Record<string, string> = {
  img: "img",
  srcset: "srcset",
  "lazy-srcset": "lazy srcset",
  lazy: "lazy-loaded",
  picture: "picture",
  "og:image": "Open Graph",
  "twitter:image": "Twitter card",
  itemprop: "itemprop",
  icon: "icon",
  css: "CSS",
  "css-inline": "inline CSS",
  "css-attr": "CSS (data-bg)",
  "json-ld": "JSON-LD",
  rendered: "JS-rendered",
  "video-poster": "video poster",
  "svg-image": "SVG",
  link: "linked",
  preload: "preload",
  direct: "direct",
};

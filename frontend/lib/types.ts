// Mirrors backend/app/schemas/scrape_schema.py

export type JobStatus =
  | "queued"
  | "fetching"
  | "rendering"
  | "extracting"
  | "downloading"
  | "completed"
  | "failed";

export type RenderMode = "auto" | "static" | "browser";

export interface ImageInfo {
  id: string;
  url: string;
  filename: string;
  mime_type: string;
  extension: string;
  size_bytes: number;
  width: number | null;
  height: number | null;
  source: string;
  preview_url: string;
  download_url: string;
}

export interface JobProgress {
  stage: JobStatus;
  message: string;
  found: number;
  processed: number;
  total: number;
  percent: number;
}

export interface JobResponse {
  job_id: string;
  url: string;
  final_url: string | null;
  page_title: string | null;
  status: JobStatus;
  render_mode: RenderMode;
  used_browser: boolean;
  progress: JobProgress;
  warnings: string[];
  error: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  image_count: number;
  total_bytes: number;
  images: ImageInfo[];
  zip_url: string | null;
}

export interface ScrapeCreated {
  job_id: string;
  status: JobStatus;
}

export const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set(["completed", "failed"]);

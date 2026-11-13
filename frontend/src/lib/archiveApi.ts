// Client for the resume archive endpoints (backend app/archive/routes.py).
// Independent of tailoring; it shares only the base URL and error handling.

import { API_BASE_URL, ApiError, toApiError } from "./api";

// Mirrors backend MAX_UPLOAD_BYTES (app/archive/service.py).
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export interface ArchivedResume {
  id: number;
  company_name: string;
  job_role: string | null;
  file_path: string;
  original_filename: string;
  file_size_bytes: number;
  upload_timestamp: string; // ISO 8601, UTC
}

const ARCHIVE_URL = `${API_BASE_URL}/api/archive`;

async function request(input: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(input, init);
  } catch {
    if (init?.signal?.aborted) throw new ApiError("Request cancelled.", "cancelled", true);
    throw new ApiError(
      `Could not reach the backend at ${API_BASE_URL}. Is it running? ` +
        "(cd backend && uv run uvicorn app.main:app --reload --port 8000)",
      "network_error",
      true,
    );
  }
  if (!response.ok) throw await toApiError(response);
  return response;
}

export async function listArchive(signal?: AbortSignal): Promise<ArchivedResume[]> {
  const response = await request(ARCHIVE_URL, { signal });
  return (await response.json()) as ArchivedResume[];
}

export async function uploadResume(input: {
  file: File;
  companyName: string;
  jobRole: string;
}): Promise<ArchivedResume> {
  const form = new FormData();
  form.append("file", input.file, input.file.name);
  form.append("company_name", input.companyName);
  if (input.jobRole.trim()) form.append("job_role", input.jobRole);
  // No Content-Type header: the browser sets multipart/form-data with the boundary.
  const response = await request(ARCHIVE_URL, { method: "POST", body: form });
  return (await response.json()) as ArchivedResume;
}

export async function deleteResume(id: number): Promise<void> {
  await request(`${ARCHIVE_URL}/${id}`, { method: "DELETE" });
}

/** Fetch the PDF and save it under its original file name. */
export async function downloadResume(record: ArchivedResume): Promise<void> {
  const response = await request(`${ARCHIVE_URL}/${record.id}/download`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = record.original_filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Client-side pre-check mirroring the backend rules; null means OK. */
export function checkPdf(file: File): string | null {
  if (!file.name.toLowerCase().endsWith(".pdf")) return "Only .pdf files are accepted.";
  if (file.type && file.type !== "application/pdf" && file.type !== "application/x-pdf") {
    return `That file's type is ${file.type}, not PDF.`;
  }
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) return "That file is larger than the 10 MB limit.";
  return null;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

const DATE_FORMAT = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

/** e.g. "Nov 12, 2026, 6:25 PM" in the user's locale and time zone. */
export function formatUploaded(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : DATE_FORMAT.format(date);
}

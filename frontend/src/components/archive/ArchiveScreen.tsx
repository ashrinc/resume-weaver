"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { deleteResume, downloadResume, listArchive, type ArchivedResume } from "@/lib/archiveApi";
import { ArchiveTable } from "./ArchiveTable";
import { ConfirmDialog } from "./ConfirmDialog";
import { UploadForm } from "./UploadForm";
import styles from "./archive.module.css";

type ListState =
  | { status: "loading" }
  | { status: "ready"; items: ArchivedResume[] }
  | { status: "error"; error: ApiError };

interface Toast {
  tone: "success" | "error";
  message: string;
}

function asApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError("Unexpected error in the browser.", "client_error", true);
}

/** Fetch the list and describe the resulting state (no React state touched). */
async function fetchList(signal?: AbortSignal): Promise<ListState> {
  try {
    return { status: "ready", items: await listArchive(signal) };
  } catch (e) {
    return { status: "error", error: asApiError(e) };
  }
}

/** All data comes from the API; nothing is persisted in the browser. */
export function ArchiveScreen() {
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [toast, setToast] = useState<Toast | null>(null);
  const [pendingDelete, setPendingDelete] = useState<ArchivedResume | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<number | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Refresh after uploads/deletes; keeps showing the current rows meanwhile.
  const loadList = useCallback(async () => {
    setList(await fetchList());
  }, []);

  // Initial load: state is set from the request's callback, never
  // synchronously in the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    const controller = new AbortController();
    void fetchList(controller.signal).then((next) => {
      if (!controller.signal.aborted) setList(next);
    });
    return () => controller.abort();
  }, []);

  useEffect(
    () => () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    },
    [],
  );

  function showToast(next: Toast) {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(next);
    toastTimer.current = setTimeout(() => setToast(null), next.tone === "success" ? 4000 : 7000);
  }

  function onUploaded(record: ArchivedResume) {
    showToast({ tone: "success", message: `Uploaded ${record.original_filename} for ${record.company_name}.` });
    void loadList();
  }

  async function onDownload(record: ArchivedResume) {
    setDownloadingId(record.id);
    try {
      await downloadResume(record);
    } catch (e) {
      const error = asApiError(e);
      showToast({ tone: "error", message: `Download failed: ${error.message}` });
      if (error.status === 404) void loadList(); // the row is gone or its file is missing
    } finally {
      setDownloadingId(null);
    }
  }

  function askDelete(record: ArchivedResume) {
    setDeleteError(null);
    setPendingDelete(record);
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteResume(pendingDelete.id);
      showToast({ tone: "success", message: `Deleted ${pendingDelete.original_filename}.` });
      setPendingDelete(null);
      void loadList();
    } catch (e) {
      const error = asApiError(e);
      if (error.status === 404) {
        // Already gone (e.g. deleted in another tab): treat as done.
        setPendingDelete(null);
        void loadList();
      } else {
        setDeleteError(error.message);
      }
    } finally {
      setDeleting(false);
    }
  }

  const count = list.status === "ready" ? list.items.length : null;

  return (
    <div className={styles.screen}>
      <section className={`card ${styles.section}`} aria-labelledby="upload-title">
        <h1 id="upload-title" className={styles.sectionTitle}>
          Upload a tailored resume
        </h1>
        <p className="muted">Keep the PDF you sent to each company. Files are stored on this machine only.</p>
        <UploadForm onUploaded={onUploaded} />
      </section>

      <section className={`card ${styles.section}`} aria-labelledby="archive-title" aria-busy={list.status === "loading"}>
        <h2 id="archive-title" className={styles.sectionTitle}>
          Archive{count !== null && <span className={styles.count}>{count}</span>}
        </h2>

        {list.status === "loading" && (
          <p className={styles.loading} role="status">
            <span className={styles.spinner} aria-hidden="true" /> Loading archive…
          </p>
        )}

        {list.status === "error" && (
          <div className={styles.errorBox} role="alert">
            <strong>Couldn&apos;t load the archive:</strong> {list.error.message}
            <div>
              <button type="button" className="btn btn-secondary btn-small" onClick={() => void loadList()}>
                Retry
              </button>
            </div>
          </div>
        )}

        {list.status === "ready" && (
          <ArchiveTable
            items={list.items}
            downloadingId={downloadingId}
            onDownload={(record) => void onDownload(record)}
            onDelete={askDelete}
          />
        )}
      </section>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete archived resume"
        message={pendingDelete ? `Delete ${pendingDelete.original_filename}? This cannot be undone.` : ""}
        confirmLabel="Delete"
        busy={deleting}
        error={deleteError}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />

      <div className={styles.toastRegion} aria-live="polite">
        {toast && (
          <div className={toast.tone === "success" ? styles.toastSuccess : styles.toastError} role="status">
            <span aria-hidden="true">{toast.tone === "success" ? "✓" : "✕"}</span> {toast.message}
          </div>
        )}
      </div>
    </div>
  );
}

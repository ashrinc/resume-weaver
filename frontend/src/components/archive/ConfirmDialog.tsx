"use client";

import { useEffect, useRef } from "react";
import styles from "./archive.module.css";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Themed confirmation modal built on <dialog>: showModal() gives a focus
 * trap, inert background and Esc-to-cancel without any library.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={styles.dialog}
      aria-labelledby="confirm-title"
      aria-describedby="confirm-message"
      onCancel={(event) => {
        event.preventDefault(); // Esc: let React state decide
        if (!busy) onCancel();
      }}
      onClick={(event) => {
        // A click on the backdrop targets the <dialog> element itself.
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
    >
      <div className={styles.dialogBody}>
        <h2 id="confirm-title" className={styles.dialogTitle}>
          {title}
        </h2>
        <p id="confirm-message" className={styles.dialogMessage}>
          {message}
        </p>
        {error && (
          <p className={styles.dialogError} role="alert">
            {error}
          </p>
        )}
        <div className={styles.dialogActions}>
          <button type="button" className="btn btn-secondary btn-small" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={`btn btn-small ${styles.btnDanger}`}
            onClick={onConfirm}
            disabled={busy}
            autoFocus
          >
            {busy && <span className={styles.spinner} aria-hidden="true" />}
            {busy ? "Deleting…" : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}

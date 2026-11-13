"use client";

import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { checkPdf, formatBytes, uploadResume, type ArchivedResume } from "@/lib/archiveApi";
import styles from "./archive.module.css";

function describeError(error: ApiError): { title: string; message: string } {
  if (error.status === 422) return { title: "Upload rejected", message: error.message };
  if (error.status !== null && error.status >= 500) {
    return {
      title: "Server error",
      message: `${error.message} Your file was not saved; please try again.`,
    };
  }
  if (error.code === "network_error") return { title: "Backend unreachable", message: error.message };
  return { title: "Upload failed", message: error.message };
}

export function UploadForm({ onUploaded }: { onUploaded: (record: ArchivedResume) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const canUpload = file !== null && company.trim() !== "" && !uploading;

  function selectFile(next: File | null) {
    setError(null);
    if (next === null) {
      setFile(null);
      setFileError(null);
      return;
    }
    const problem = checkPdf(next);
    setFile(problem ? null : next);
    setFileError(problem);
    if (problem && inputRef.current) inputRef.current.value = "";
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragOver(false);
    const files = event.dataTransfer.files;
    if (files.length > 1) {
      setFile(null);
      setFileError("Drop one PDF at a time.");
      return;
    }
    selectFile(files[0] ?? null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canUpload || file === null) return;
    setUploading(true);
    setError(null);
    try {
      const record = await uploadResume({ file, companyName: company.trim(), jobRole: role });
      setFile(null);
      setCompany("");
      setRole("");
      if (inputRef.current) inputRef.current.value = "";
      onUploaded(record);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError("Unexpected error in the browser.", "client_error", true));
    } finally {
      setUploading(false);
    }
  }

  const described = error ? describeError(error) : null;

  return (
    <form className={styles.form} onSubmit={submit} noValidate>
      <label
        className={`${styles.dropzone} ${dragOver ? styles.dropzoneActive : ""} ${file ? styles.dropzoneFilled : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,application/pdf"
          className="sr-only"
          onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
          aria-describedby="file-help"
        />
        <span className={styles.dropIcon} aria-hidden="true">
          {file ? "✓" : "⇪"}
        </span>
        {file ? (
          <span className={styles.dropText}>
            <strong>{file.name}</strong> <span className="muted">({formatBytes(file.size)}) · click to change</span>
          </span>
        ) : (
          <span className={styles.dropText}>
            <strong>Drop a PDF here</strong> <span className="muted">or click to choose one</span>
          </span>
        )}
        <span id="file-help" className={styles.dropHelp}>
          PDF only, up to 10 MB
        </span>
      </label>
      {fileError && (
        <p className={styles.fieldError} role="alert">
          {fileError}
        </p>
      )}

      <div className={styles.fields}>
        <div className={styles.field}>
          <label htmlFor="company-name">
            Company name <span className={styles.required}>*</span>
          </label>
          <input
            id="company-name"
            className="input"
            value={company}
            onChange={(event) => setCompany(event.target.value)}
            maxLength={200}
            required
            autoComplete="organization"
            placeholder="e.g. Acme Corp"
          />
        </div>
        <div className={styles.field}>
          <label htmlFor="job-role">
            Job role <span className="muted">(optional)</span>
          </label>
          <input
            id="job-role"
            className="input"
            value={role}
            onChange={(event) => setRole(event.target.value)}
            maxLength={200}
            autoComplete="organization-title"
            placeholder="e.g. Senior Backend Engineer"
          />
        </div>
      </div>

      <div className={styles.formActions}>
        <button type="submit" className="btn btn-primary" disabled={!canUpload}>
          {uploading && <span className={styles.spinnerDark} aria-hidden="true" />}
          {uploading ? "Uploading…" : "Upload"}
        </button>
        {!file && !uploading && <span className="muted">Choose a PDF and enter a company name.</span>}
      </div>

      {described && (
        <div className={styles.errorBox} role="alert">
          <strong>{described.title}:</strong> {described.message}
        </div>
      )}
    </form>
  );
}

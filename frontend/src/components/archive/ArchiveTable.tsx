import { formatBytes, formatUploaded, type ArchivedResume } from "@/lib/archiveApi";
import styles from "./archive.module.css";

interface ArchiveTableProps {
  items: ArchivedResume[];
  downloadingId: number | null;
  onDownload: (record: ArchivedResume) => void;
  onDelete: (record: ArchivedResume) => void;
}

/** Rows arrive newest-first from the API and are rendered in that order. */
export function ArchiveTable({ items, downloadingId, onDownload, onDelete }: ArchiveTableProps) {
  if (items.length === 0) {
    return <p className={styles.empty}>No archived resumes yet. Upload your first one above.</p>;
  }
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption className="sr-only">Archived resumes, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Company Name</th>
            <th scope="col">Job Role</th>
            <th scope="col">Original Filename</th>
            <th scope="col" className={styles.numeric}>
              Size
            </th>
            <th scope="col">Uploaded</th>
            <th scope="col" className="sr-only">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td className={styles.company}>{item.company_name}</td>
              <td>{item.job_role ?? <span className="muted">—</span>}</td>
              <td className={styles.filename} title={item.original_filename}>
                {item.original_filename}
              </td>
              <td className={styles.numeric}>{formatBytes(item.file_size_bytes)}</td>
              <td className={styles.date}>
                <time dateTime={item.upload_timestamp} title={item.upload_timestamp}>
                  {formatUploaded(item.upload_timestamp)}
                </time>
              </td>
              <td className={styles.actions}>
                <button
                  type="button"
                  className={`btn btn-secondary btn-small ${styles.rowButton}`}
                  onClick={() => onDownload(item)}
                  disabled={downloadingId === item.id}
                  aria-label={`Download ${item.original_filename}`}
                >
                  {downloadingId === item.id ? <span className={styles.spinner} aria-hidden="true" /> : "↓"}{" "}
                  Download
                </button>
                <button
                  type="button"
                  className={`btn btn-secondary btn-small ${styles.rowButton} ${styles.rowDelete}`}
                  onClick={() => onDelete(item)}
                  aria-label={`Delete ${item.original_filename}`}
                >
                  ✕ Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

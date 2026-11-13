"use client";

import { useMemo, useRef, type KeyboardEvent, type UIEvent } from "react";
import { tokenizeLatex } from "@/lib/latexHighlight";
import styles from "./tailor.module.css";

function Highlighted({ source }: { source: string }) {
  const tokens = useMemo(() => tokenizeLatex(source), [source]);
  return (
    <>
      {tokens.map((token, i) => (
        <span key={i} className={styles[`tok-${token.type}`]}>
          {token.text}
        </span>
      ))}
    </>
  );
}

/** Read-only, syntax-highlighted LaTeX. */
export function LatexView({ source, label }: { source: string; label: string }) {
  return (
    <pre className={styles.codeView} aria-label={label} tabIndex={0}>
      <code>
        <Highlighted source={source} />
      </code>
    </pre>
  );
}

interface LatexEditorProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  onKeyDown?: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  placeholder?: string;
  describedBy?: string;
}

/**
 * Editable LaTeX with live highlighting: a transparent <textarea> on top of a
 * highlighted <pre> with identical font metrics. The textarea owns input,
 * caret and selection; the <pre> follows its scroll position.
 */
export function LatexEditor({ id, value, onChange, onKeyDown, placeholder, describedBy }: LatexEditorProps) {
  const highlightRef = useRef<HTMLPreElement>(null);

  const syncScroll = (event: UIEvent<HTMLTextAreaElement>) => {
    const pre = highlightRef.current;
    if (pre) {
      pre.scrollTop = event.currentTarget.scrollTop;
      pre.scrollLeft = event.currentTarget.scrollLeft;
    }
  };

  return (
    <div className={styles.editor}>
      <pre ref={highlightRef} className={styles.editorHighlight} aria-hidden="true">
        <code>
          <Highlighted source={value} />
          {/* Keeps the last line's height in step with the textarea. */}
          {"\n"}
        </code>
      </pre>
      <textarea
        id={id}
        className={styles.editorInput}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onScroll={syncScroll}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        aria-describedby={describedBy}
        spellCheck={false}
        autoCapitalize="off"
        autoComplete="off"
        autoCorrect="off"
        wrap="off"
      />
    </div>
  );
}

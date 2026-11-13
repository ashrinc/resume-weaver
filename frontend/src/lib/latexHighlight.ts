// Tiny LaTeX tokenizer for display-only syntax highlighting (no dependencies).
// It never changes the text: joining all token texts gives back the input,
// which the editor overlay relies on to keep the caret aligned.

export type TokenType = "text" | "command" | "comment" | "brace" | "bracket" | "math";

export interface Token {
  type: TokenType;
  text: string;
}

// Order matters: comments first, then escaped characters / control words.
const TOKEN_RE =
  /(%[^\n]*)|(\\[A-Za-z@]+\*?|\\[^A-Za-z@\n])|([{}])|([[\]])|(\$\$?)/g;

export function tokenizeLatex(source: string): Token[] {
  const tokens: Token[] = [];
  let last = 0;
  for (const m of source.matchAll(TOKEN_RE)) {
    const index = m.index ?? 0;
    if (index > last) tokens.push({ type: "text", text: source.slice(last, index) });
    const type: TokenType = m[1]
      ? "comment"
      : m[2]
        ? "command"
        : m[3]
          ? "brace"
          : m[4]
            ? "bracket"
            : "math";
    tokens.push({ type, text: m[0] });
    last = index + m[0].length;
  }
  if (last < source.length) tokens.push({ type: "text", text: source.slice(last) });
  return tokens;
}

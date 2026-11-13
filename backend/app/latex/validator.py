"""Basic LaTeX syntax validation (PRD FR-07).

A single left-to-right scan of the source that:

* skips comments (an unescaped ``%`` to end of line),
* skips escaped characters (``\\{``, ``\\}``, ``\\[``, ``\\%`` ...) via the
  backslash rule: a backslash always consumes the following character, or the
  whole control word if it is a letter,
* tracks ``{}`` and ``[]`` balance (errors) and ``()`` balance (warnings only,
  since prose such as "1) foo" legitimately has stray parentheses),
* matches ``\\begin{x}`` / ``\\end{x}`` with a stack,
* records whether ``\\documentclass``, ``\\begin{document}`` and
  ``\\end{document}`` appear outside comments.

Each delimiter type is balanced independently, so ``{[}`` (a literal bracket
inside a group) leaves the brace check unaffected.

This is a heuristic check, not a LaTeX compiler: verbatim-like environments
and ``\\verb`` are not special-cased.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

IssueCode = Literal[
    "unmatched_brace",
    "unmatched_bracket",
    "unmatched_paren",
    "unmatched_begin",
    "unmatched_end",
    "mismatched_environment",
    "missing_documentclass",
    "missing_begin_document",
    "missing_end_document",
]

_OPENERS = {"{": "}", "[": "]", "(": ")"}
_CLOSERS = {v: k for k, v in _OPENERS.items()}
_DELIM_CODE: dict[str, IssueCode] = {
    "{": "unmatched_brace",
    "[": "unmatched_bracket",
    "(": "unmatched_paren",
}
_DELIM_NAME = {"{": "brace", "[": "bracket", "(": "parenthesis"}


class ValidationIssue(BaseModel):
    code: IssueCode
    message: str
    line: int | None = Field(default=None, description="1-based line number, if applicable")


class ValidationResult(BaseModel):
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_latex(source: str) -> ValidationResult:
    """Validate ``source`` and return all errors and warnings, sorted by line."""
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    # One stack per delimiter type; each entry is the line of the opener.
    delim_stacks: dict[str, list[int]] = {"{": [], "[": [], "(": []}
    # Environment stack entries: (name, line of \begin).
    env_stack: list[tuple[str, int]] = []

    has_documentclass = False
    has_begin_document = False
    has_end_document = False

    def report(opener: str, message: str, line: int) -> None:
        issue = ValidationIssue(code=_DELIM_CODE[opener], message=message, line=line)
        (warnings if opener == "(" else errors).append(issue)

    i = 0
    n = len(source)
    line = 1

    while i < n:
        ch = source[i]

        if ch == "\n":
            line += 1
            i += 1

        elif ch == "%":
            # Comment: skip to (but not past) the newline so it is counted above.
            nl = source.find("\n", i)
            i = n if nl == -1 else nl

        elif ch == "\\":
            i += 1
            if i >= n:
                break
            if not source[i].isalpha():
                # Control symbol (\{, \%, \\, \[, ...): consume exactly one char.
                if source[i] == "\n":
                    line += 1
                i += 1
                continue

            start = i
            while i < n and source[i].isalpha():
                i += 1
            word = source[start:i]
            cmd_line = line

            if word == "documentclass":
                has_documentclass = True
            elif word in ("begin", "end"):
                name, i, line = _read_env_name(source, i, line)
                if name is None:
                    # Malformed \begin/\end; let the generic scan handle what follows.
                    continue
                if word == "begin":
                    env_stack.append((name, cmd_line))
                    if name == "document":
                        has_begin_document = True
                else:
                    if name == "document":
                        has_end_document = True
                    _close_env(name, cmd_line, env_stack, errors)

        elif ch in _OPENERS:
            delim_stacks[ch].append(line)
            i += 1

        elif ch in _CLOSERS:
            opener = _CLOSERS[ch]
            if delim_stacks[opener]:
                delim_stacks[opener].pop()
            else:
                report(opener, f"Unexpected closing {_DELIM_NAME[opener]} '{ch}'", line)
            i += 1

        else:
            i += 1

    for opener, stack in delim_stacks.items():
        for open_line in stack:
            report(opener, f"Unclosed {_DELIM_NAME[opener]} '{opener}'", open_line)

    for name, open_line in env_stack:
        errors.append(
            ValidationIssue(
                code="unmatched_begin",
                message=f"\\begin{{{name}}} is never closed",
                line=open_line,
            )
        )

    if not has_documentclass:
        errors.append(
            ValidationIssue(code="missing_documentclass", message="Missing \\documentclass")
        )
    if not has_begin_document:
        errors.append(
            ValidationIssue(code="missing_begin_document", message="Missing \\begin{document}")
        )
    if not has_end_document:
        errors.append(
            ValidationIssue(code="missing_end_document", message="Missing \\end{document}")
        )

    def sort_key(issue: ValidationIssue) -> tuple[int, int]:
        # Line-numbered issues first in line order; document-level issues last.
        return (0, issue.line) if issue.line is not None else (1, 0)

    errors.sort(key=sort_key)
    warnings.sort(key=sort_key)
    return ValidationResult(errors=errors, warnings=warnings)


def _read_env_name(source: str, i: int, line: int) -> tuple[str | None, int, int]:
    """Read ``{name}`` after ``\\begin``/``\\end``, allowing whitespace before ``{``.

    Returns ``(name, new_index, new_line)``. If the argument is missing or not
    closed, returns ``(None, i, line)`` unchanged so the caller rescans it.
    """
    j, ln = i, line
    while j < len(source) and source[j] in " \t\n":
        if source[j] == "\n":
            ln += 1
        j += 1
    if j >= len(source) or source[j] != "{":
        return None, i, line
    close = source.find("}", j + 1)
    if close == -1 or "\n" in source[j + 1 : close]:
        return None, i, line
    name = source[j + 1 : close].strip()
    if not name:
        return None, i, line
    return name, close + 1, ln


def _close_env(
    name: str,
    line: int,
    env_stack: list[tuple[str, int]],
    errors: list[ValidationIssue],
) -> None:
    if not env_stack:
        errors.append(
            ValidationIssue(
                code="unmatched_end",
                message=f"\\end{{{name}}} without a matching \\begin{{{name}}}",
                line=line,
            )
        )
        return

    top_name, top_line = env_stack[-1]
    if top_name == name:
        env_stack.pop()
        return

    if any(env_name == name for env_name, _ in env_stack):
        # Recover: everything opened after the matching \begin was left unclosed.
        while env_stack[-1][0] != name:
            open_name, open_line = env_stack.pop()
            errors.append(
                ValidationIssue(
                    code="mismatched_environment",
                    message=(
                        f"\\begin{{{open_name}}} (line {open_line}) closed by "
                        f"\\end{{{name}}} on line {line}"
                    ),
                    line=open_line,
                )
            )
        env_stack.pop()
    else:
        errors.append(
            ValidationIssue(
                code="mismatched_environment",
                message=(
                    f"\\end{{{name}}} does not match open \\begin{{{top_name}}} (line {top_line})"
                ),
                line=line,
            )
        )

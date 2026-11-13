from textwrap import dedent

from app.latex.validator import ValidationResult, validate_latex


def doc(body: str) -> str:
    """Wrap a body in a minimal valid document. The body starts on line 4."""
    return (
        "\\documentclass{article}\n\\usepackage{enumitem}\n\\begin{document}\n"
        + body
        + ("\n\\end{document}\n")
    )


def codes(issues) -> list[str]:
    return [i.code for i in issues]


# --- valid input ------------------------------------------------------------


def test_valid_resume_like_document_has_no_issues() -> None:
    src = dedent(
        r"""
        \documentclass[letterpaper,11pt]{article}
        \newcommand{\resumeItem}[1]{\item\small{#1}}
        \begin{document}
        \section{Experience}
        \begin{itemize}[leftside=0pt]
          \resumeItem{Reduced latency by 40\% (p99) using \textbf{Redis}}
          \resumeItem{Built CI/CD pipelines [GitHub Actions]}
        \end{itemize}
        \end{document}
        """
    )
    result = validate_latex(src)
    assert result == ValidationResult()
    assert result.is_valid


def test_nested_environments_are_valid() -> None:
    result = validate_latex(
        doc("\\begin{itemize}\n\\begin{tabular}{ll}\\end{tabular}\n\\end{itemize}")
    )
    assert result.errors == []


# --- delimiters -------------------------------------------------------------


def test_unclosed_brace_reports_opening_line() -> None:
    result = validate_latex(doc("ok\n\\textbf{oops"))
    assert codes(result.errors) == ["unmatched_brace"]
    assert result.errors[0].line == 5


def test_unexpected_closing_brace_reports_its_line() -> None:
    result = validate_latex(doc("text}"))
    assert codes(result.errors) == ["unmatched_brace"]
    assert result.errors[0].line == 4
    assert "Unexpected closing" in result.errors[0].message


def test_unmatched_brackets_are_errors() -> None:
    result = validate_latex(doc("\\item[ unclosed\nthen ]]"))
    # The '[' on line 4 is closed by the first ']' on line 5; the second is stray.
    assert codes(result.errors) == ["unmatched_bracket"]
    assert result.errors[0].line == 5


def test_unbalanced_parentheses_are_warnings_only() -> None:
    result = validate_latex(doc("1) first item\n(unclosed"))
    assert result.errors == []
    assert result.is_valid
    assert codes(result.warnings) == ["unmatched_paren", "unmatched_paren"]
    assert [w.line for w in result.warnings] == [4, 5]


def test_delimiter_types_are_balanced_independently() -> None:
    # A literal '[' inside a group, closed later, must not break the brace check.
    assert validate_latex(doc("{[} text {]}")).errors == []


# --- escapes and comments -----------------------------------------------------


def test_escaped_delimiters_are_ignored() -> None:
    result = validate_latex(doc(r"\{ \} \[ x \] \( y \) \$ \&"))
    assert result.errors == []
    assert result.warnings == []


def test_double_backslash_does_not_escape_following_brace() -> None:
    # "\\{" is a line break followed by a real, unclosed brace.
    result = validate_latex(doc(r"line \\{"))
    assert codes(result.errors) == ["unmatched_brace"]


def test_comments_are_ignored() -> None:
    result = validate_latex(doc("text % { [ ( \\begin{itemize} \\end{foo}\nmore"))
    assert result.errors == []
    assert result.warnings == []


def test_escaped_percent_is_not_a_comment() -> None:
    result = validate_latex(doc(r"50\% {unclosed"))
    assert codes(result.errors) == ["unmatched_brace"]


def test_percent_after_double_backslash_starts_a_comment() -> None:
    result = validate_latex(doc(r"line \\% {ignored"))
    assert result.errors == []


def test_commented_out_required_commands_do_not_count() -> None:
    src = "% \\documentclass{article}\n\\begin{document}\nx\n% \\end{document}\n"
    result = validate_latex(src)
    # With \end{document} commented out, \begin{document} is also left open.
    assert codes(result.errors) == [
        "unmatched_begin",
        "missing_documentclass",
        "missing_end_document",
    ]
    assert result.errors[0].line == 2


# --- environments -------------------------------------------------------------


def test_unclosed_begin_reports_its_line() -> None:
    result = validate_latex(doc("a\n\\begin{itemize}\n\\item x"))
    # \end{document} closes past the open itemize -> reported as mismatched.
    assert codes(result.errors) == ["mismatched_environment"]
    assert result.errors[0].line == 5
    assert "itemize" in result.errors[0].message


def test_unclosed_begin_at_end_of_file() -> None:
    src = "\\documentclass{article}\n\\begin{document}\n\\end{document}\n\\begin{center}\n"
    result = validate_latex(src)
    assert codes(result.errors) == ["unmatched_begin"]
    assert result.errors[0].line == 4


def test_end_without_begin() -> None:
    src = "\\documentclass{article}\n\\end{itemize}\n\\begin{document}\n\\end{document}\n"
    result = validate_latex(src)
    assert codes(result.errors) == ["unmatched_end"]
    assert result.errors[0].line == 2


def test_mismatched_end_name() -> None:
    result = validate_latex(doc("\\begin{itemize}\n\\end{enumerate}\n\\end{itemize}"))
    assert codes(result.errors) == ["mismatched_environment"]
    assert result.errors[0].line == 5
    assert "enumerate" in result.errors[0].message


def test_env_name_with_whitespace_and_line_break() -> None:
    result = validate_latex(doc("\\begin {itemize}\n\\end\n{itemize}\nafter }"))
    # Line tracking survives the newline inside "\end\n{itemize}".
    assert codes(result.errors) == ["unmatched_brace"]
    assert result.errors[0].line == 7


# --- required commands ----------------------------------------------------------


def test_missing_required_commands() -> None:
    result = validate_latex("just some text {}\n")
    assert codes(result.errors) == [
        "missing_documentclass",
        "missing_begin_document",
        "missing_end_document",
    ]
    assert all(e.line is None for e in result.errors)


def test_empty_input() -> None:
    result = validate_latex("")
    assert len(result.errors) == 3
    assert not result.is_valid


# --- ordering ------------------------------------------------------------------


def test_issues_sorted_by_line_with_document_level_last() -> None:
    src = "x }\n\\begin{document}\ny {\n"
    result = validate_latex(src)
    assert [e.line for e in result.errors] == [1, 2, 3, None, None]
    assert codes(result.errors)[-2:] == ["missing_documentclass", "missing_end_document"]

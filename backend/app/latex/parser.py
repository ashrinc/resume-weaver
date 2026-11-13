"""Rule-based LaTeX resume parser (PRD FR-02).

How it works
------------
1. ``mask_latex`` builds a copy of the source with the *same length* in which
   comments, escaped characters (``\\{``, ``\\%``, ``\\\\`` ...), ``\\verb|..|``
   and verbatim-like environments are blanked to spaces. All structure
   detection runs on this masked copy, so a ``\\section`` in a comment or in
   escaped text can never match, yet every offset still lines up with the
   original source.
2. The preamble is everything up to and including ``\\begin{document}``.
3. The body is cut at each top-level ``\\section`` / ``\\section*`` into
   segments that cover every character. ``reconstruct(parse_resume(x)) == x``
   always holds because segment text is sliced straight from the source.
4. Each section segment is parsed into entries, bullets, skills and
   certifications. If anything about a section fails to parse, that segment
   becomes ``kind="raw"`` and is passed through untouched.

Supported constructs: ``\\section``, ``\\section*``, ``\\subsection`` (as an
entry heading), Jake's Resume macros (``\\resumeSubheading``,
``\\resumeSubSubheading``, ``\\resumeProjectHeading``, ``\\resumeItem``),
moderncv (``\\cventry``, ``\\cvitem``, ``\\cvlistitem``), and ``itemize`` /
``\\item``.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from app.jd.taxonomy import get_taxonomy
from app.latex.models import (
    Bullet,
    Entry,
    ParsedResume,
    SectionContent,
    SectionType,
    Segment,
    SkillGroup,
)

__all__ = [
    "classify_section",
    "extract_metrics",
    "latex_to_plain",
    "mask_latex",
    "parse_dates",
    "parse_resume",
    "reconstruct",
]


class _ParseError(Exception):
    """A section could not be parsed reliably; it will be passed through raw."""


# --------------------------------------------------------------------------
# Masking
# --------------------------------------------------------------------------

_VERBATIM_BEGIN_RE = re.compile(r"\s*\{(verbatim|Verbatim|lstlisting|minted|comment)(\*?)\}")


def mask_latex(source: str) -> str:
    """Return ``source`` with comments, escapes and verbatim text blanked.

    The result has the same length as ``source`` and newlines are preserved,
    so offsets and line numbers are unchanged. Control words such as
    ``\\section`` are kept; control symbols such as ``\\{`` become spaces.
    """
    out = list(source)
    n = len(source)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        ch = source[i]
        if ch == "%":
            nl = source.find("\n", i)
            stop = n if nl == -1 else nl
            blank(i, stop)
            i = stop
        elif ch == "\\":
            if i + 1 >= n:
                blank(i, i + 1)
                i += 1
                continue
            if not source[i + 1].isalpha():
                blank(i, i + 2)  # \{  \}  \%  \\  \$ ...
                i += 2
                continue
            j = i + 1
            while j < n and source[j].isalpha():
                j += 1
            word = source[i + 1 : j]
            if word == "verb":
                k = j + 1 if j < n and source[j] == "*" else j
                if k < n and not source[k].isalnum() and not source[k].isspace():
                    close = source.find(source[k], k + 1)
                    nl = source.find("\n", k + 1)
                    if close != -1 and (nl == -1 or close < nl):
                        blank(i, close + 1)
                        i = close + 1
                        continue
            elif word == "begin":
                m = _VERBATIM_BEGIN_RE.match(source, j)
                if m:
                    end_re = re.compile(r"\\end\s*\{" + re.escape(m.group(1) + m.group(2)) + r"\}")
                    e = end_re.search(source, m.end())
                    stop = e.start() if e else n
                    blank(m.end(), stop)
                    i = stop
                    continue
            i = j
        else:
            i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# Plain-text conversion
# --------------------------------------------------------------------------

_PAR = "\x01"  # line-break marker used during conversion
_DOLLAR, _LBRACE, _RBRACE = "\x02", "\x03", "\x04"
_CVITEM_RE = re.compile(r"\\cvitem(?:withcomment)?\s*\{([^{}]*)\}\s*\{")


def _strip_comments(latex: str) -> str:
    """Remove ``%`` comments (TeX-style: the comment also eats the newline)."""
    out: list[str] = []
    i, n = 0, len(latex)
    while i < n:
        ch = latex[i]
        if ch == "\\" and i + 1 < n:
            out.append(latex[i : i + 2])
            i += 2
        elif ch == "%":
            nl = latex.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            while i < n and latex[i] in " \t":
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def latex_to_plain(latex: str) -> str:
    """Best-effort LaTeX to plain text. Line breaks (``\\\\``, ``\\item``,
    environments, blank lines) become newlines; formatting commands are
    dropped and their arguments kept."""
    s = _strip_comments(latex.replace("\r\n", "\n"))
    s = re.sub(r"\n[ \t]*\n\s*", _PAR, s)
    s = s.replace("\n", " ")
    s = re.sub(r"\\\\\*?(?:\[[^\]]*\])?", _PAR, s)
    s = s.replace("\\$", _DOLLAR).replace("\\{", _LBRACE).replace("\\}", _RBRACE)
    for esc, rep in (("\\&", "&"), ("\\%", "%"), ("\\#", "#"), ("\\_", "_")):
        s = s.replace(esc, rep)
    s = s.replace("$|$", " | ").replace("$", "")
    s = re.sub(r"\\textbar(?![a-zA-Z])", " | ", s)
    s = re.sub(r"\\href\s*\{[^{}]*\}", "", s)
    s = re.sub(r"\\[vh]space\*?\s*\{[^{}]*\}", " ", s)
    s = re.sub(r"\\(?:setlength|addtolength)\s*\{[^{}]*\}\s*\{[^{}]*\}", "", s)
    s = re.sub(r"\\(?:hfill|quad|qquad)(?![a-zA-Z])", " ", s)
    s = re.sub(r"\\(?:newline|par|linebreak|cvlistitem|cvlistdoubleitem)(?![a-zA-Z])", _PAR, s)
    s = re.sub(r"\\item(?![a-zA-Z])(?:\s*\[[^\]]*\])?", _PAR, s)
    s = re.sub(r"\\(?:begin|end)\s*\{[^{}]*\}(?:\[[^\]]*\])?", _PAR, s)
    s = _CVITEM_RE.sub(
        lambda m: _PAR + (f"{m.group(1).strip()}: " if m.group(1).strip() else ""), s
    )
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", "", s)  # remaining control words
    s = re.sub(r"\\[ ,;:!]", " ", s)
    s = re.sub(r"\\.", "", s)
    s = s.replace("}{", " ").replace("{", "").replace("}", "").replace("~", " ")
    s = s.replace("---", "—").replace("--", "–").replace("``", '"').replace("''", '"')
    s = s.replace(_DOLLAR, "$").replace(_LBRACE, "{").replace(_RBRACE, "}")

    lines = []
    for line in s.split(_PAR):
        line = re.sub(r"\s+", " ", line).strip()
        line = re.sub(r"\s+([,.;:!?)\]%])", r"\1", line)
        line = re.sub(r"([(\[])\s+", r"\1", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def _inline(latex: str) -> str:
    return " ".join(latex_to_plain(latex).split())


# --------------------------------------------------------------------------
# Dates, metrics, section classification
# --------------------------------------------------------------------------

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_SEASON = r"(?:spring|summer|fall|autumn|winter)"
_DATE = rf"(?:(?:{_MONTH}|{_SEASON})\s+\d{{4}}|\d{{1,2}}/\d{{4}}|(?:19|20)\d{{2}})"
_PRESENT = r"(?:present|current|now|ongoing|today)"
_RANGE_RE = re.compile(
    rf"(?<!\w)(?P<start>{_DATE})\s*(?:–|—|-{{1,3}}|to|until)\s*(?P<end>{_DATE}|{_PRESENT})(?!\w)",
    re.IGNORECASE,
)
_SINGLE_DATE_RE = re.compile(rf"(?<!\w){_DATE}(?!\w)", re.IGNORECASE)


class Dates(NamedTuple):
    text: str
    start: str | None
    end: str | None


def parse_dates(text: str) -> Dates | None:
    """Find a date range ('Aug. 2018 -- May 2021', '2021–Present') or a single
    date ('May 2021', treated as the end date) in plain text."""
    m = _RANGE_RE.search(text)
    if m:
        return Dates(m.group(0), m.group("start"), m.group("end"))
    m = _SINGLE_DATE_RE.search(text)
    if m:
        return Dates(m.group(0), None, m.group(0))
    return None


def _is_date_text(text: str) -> bool:
    d = parse_dates(text)
    return d is not None and len(d.text) >= 0.5 * len(text.strip())


_METRIC_RE = re.compile(
    r"(?<![\w.$])(?:"
    r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[KMB](?![a-zA-Z]))?\+?"
    r"|\d[\d,]*(?:\.\d+)?(?:\s?%|[xX](?![a-zA-Z])|\s?[KMBkmb](?![a-zA-Z])\+?|\+)?"
    r")"
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


_BARE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)*")
# Up to three words ending right before the number, separated only by spaces.
_PRECEDING_WORDS_RE = re.compile(r"([A-Za-z][\w.+#/-]*(?:[ \t]+[A-Za-z][\w.+#/-]*){0,2})[ \t]+$")
_VERSION_WORDS = {"v", "ver", "ver.", "version", "release", "update"}


def _is_version_number(text: str, start: int, value: str) -> bool:
    """True for a bare number directly after a tech name or 'version':
    'Python 3.11', 'Node 18', 'Spring Boot 3', 'version 2'."""
    if not _BARE_NUMBER_RE.fullmatch(value):
        return False  # 40%, 3x, 10K+, $5M are real metrics
    m = _PRECEDING_WORDS_RE.search(text[max(0, start - 60) : start])
    if m is None:
        return False
    words = m.group(1).split()
    taxonomy = get_taxonomy()
    for k in range(1, len(words) + 1):
        phrase = " ".join(words[-k:])
        if phrase.lower() in _VERSION_WORDS or taxonomy.is_tech_term(phrase):
            return True
    return False


def extract_metrics(text: str) -> list[str]:
    """Return quantified values (percentages, money, counts, multipliers) in
    ``text``. Ignored: bare years, digits inside words (EC2, k8s) and version
    numbers after a technology name (Python 3.11, Node 18)."""
    found = []
    for m in _METRIC_RE.finditer(text):
        value = m.group(0).rstrip(",").strip()
        if not value or _YEAR_RE.fullmatch(value):
            continue
        if _is_version_number(text, m.start(), value):
            continue
        found.append(value)
    return found


_SECTION_RULES: list[tuple[SectionType, tuple[str, ...]]] = [
    ("projects", ("project",)),
    ("education", ("education", "academic")),
    # "career history" rather than bare "career", so "Career Objective" and
    # "Career Summary" fall through to summary.
    ("experience", ("experience", "employment", "work history", "internship", "career history")),
    ("skills", ("skill", "technolog", "tools", "competenc", "expertise", "tech stack")),
    ("summary", ("summary", "objective", "profile", "about")),
]
_CERT_TITLE_RE = re.compile(r"certif|licen[cs]", re.IGNORECASE)


def classify_section(title: str) -> SectionType:
    """Map a section heading to a section type (first matching rule wins)."""
    t = title.casefold()
    for section_type, keywords in _SECTION_RULES:
        if any(k in t for k in keywords):
            return section_type
    return "other"


# --------------------------------------------------------------------------
# Low-level scanning helpers (all operate on the masked text)
# --------------------------------------------------------------------------


class _Group(NamedTuple):
    start: int  # inner start (after '{')
    end: int  # inner end (index of '}')
    after: int  # index after '}'


def _skip_ws(m: str, i: int, limit: int) -> int:
    while i < limit and m[i].isspace():
        i += 1
    return i


def _read_group(m: str, i: int, limit: int) -> _Group | None:
    i = _skip_ws(m, i, limit)
    if i >= limit or m[i] != "{":
        return None
    depth = 0
    for j in range(i, limit):
        if m[j] == "{":
            depth += 1
        elif m[j] == "}":
            depth -= 1
            if depth == 0:
                return _Group(i + 1, j, j + 1)
    return None


def _skip_optional(m: str, i: int, limit: int) -> int:
    """Skip an optional ``[...]`` argument if present."""
    j = _skip_ws(m, i, limit)
    if j < limit and m[j] == "[":
        depth = 0
        for k in range(j, limit):
            if m[k] == "{":
                depth += 1
            elif m[k] == "}":
                depth -= 1
            elif m[k] == "]" and depth == 0:
                return k + 1
    return i


def _read_args(m: str, i: int, limit: int, count: int) -> list[_Group]:
    groups = []
    for _ in range(count):
        g = _read_group(m, i, limit)
        if g is None:
            raise _ParseError(f"expected {count} {{...}} arguments")
        groups.append(g)
        i = g.after
    return groups


def _balanced(m: str, a: int, b: int) -> bool:
    depth = 0
    for k in range(a, b):
        if m[k] == "{":
            depth += 1
        elif m[k] == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _line_start(m: str, pos: int) -> int | None:
    """Start of the line containing ``pos`` if only whitespace precedes ``pos``."""
    k = m.rfind("\n", 0, pos) + 1
    return k if m[k:pos].strip() == "" else None


# --------------------------------------------------------------------------
# Top-level segmentation
# --------------------------------------------------------------------------

_BEGIN_DOC_RE = re.compile(r"\\begin\s*\{document\}")
_END_DOC_RE = re.compile(r"\\end\s*\{document\}")
_STRUCT_RE = re.compile(
    r"\{|\}|\\begin\s*\{([^{}]*)\}|\\end\s*\{([^{}]*)\}|\\section\*?(?![a-zA-Z])"
)
_SECTION_CMD_RE = re.compile(r"\\section\*?")


def _find_section_commands(m: str, start: int, stop: int) -> list[int]:
    """Positions of real top-level ``\\section`` commands in ``m[start:stop]``.

    A section must not be inside an environment, and must be either at brace
    depth 0 or the first thing on its line. The line-start rule keeps later
    sections detectable after an earlier section has an unclosed brace.
    """
    found: list[int] = []
    depth = 0
    envs: list[str] = []
    for tok in _STRUCT_RE.finditer(m, start, stop):
        text = tok.group(0)
        if text == "{":
            depth += 1
        elif text == "}":
            depth = max(depth - 1, 0)
        elif tok.group(1) is not None:
            envs.append(tok.group(1).strip())
        elif tok.group(2) is not None:
            name = tok.group(2).strip()
            if name in envs:
                while envs.pop() != name:
                    pass
        elif not envs and (depth == 0 or _line_start(m, tok.start()) is not None):
            found.append(tok.start())
    return found


def parse_resume(source: str) -> ParsedResume:
    """Parse LaTeX resume source. Never raises for malformed input."""
    masked = mask_latex(source)
    n = len(source)

    begin = _BEGIN_DOC_RE.search(masked)
    if begin is None:
        segments = [_raw(source, 0, n, "document", "no \\begin{document} found")] if source else []
        return ParsedResume(preamble="", segments=segments, has_document_body=False)

    body_start = begin.end()
    ends = list(_END_DOC_RE.finditer(masked, body_start))
    if ends:
        end_pos = ends[-1].start()
        tail_start = max(_line_start(masked, end_pos) or end_pos, body_start)
    else:
        tail_start = n

    bounds: list[tuple[int, int]] = []  # (segment start, \section position)
    for pos in _find_section_commands(masked, body_start, tail_start):
        seg_start = _line_start(masked, pos)
        seg_start = pos if seg_start is None else seg_start
        floor = bounds[-1][1] + 1 if bounds else body_start
        bounds.append((max(seg_start, floor), pos))

    segments: list[Segment] = []
    first = bounds[0][0] if bounds else tail_start
    if first > body_start:
        segments.append(_raw(source, body_start, first, "header" if bounds else "body"))
    for idx, (seg_start, cmd) in enumerate(bounds):
        seg_end = bounds[idx + 1][0] if idx + 1 < len(bounds) else tail_start
        segments.append(_parse_section(source, masked, seg_start, cmd, seg_end))
    if tail_start < n:
        segments.append(_raw(source, tail_start, n, "end_document"))

    skills: list[str] = []
    certs: list[str] = []
    seen_skills: set[str] = set()
    seen_certs: set[str] = set()
    for seg in segments:
        if seg.content is None:
            continue
        for group in seg.content.skill_groups:
            for skill in group.skills:
                if skill.casefold() not in seen_skills:
                    seen_skills.add(skill.casefold())
                    skills.append(skill)
        for cert in seg.content.certifications:
            if cert.casefold() not in seen_certs:
                seen_certs.add(cert.casefold())
                certs.append(cert)

    return ParsedResume(
        preamble=source[:body_start],
        segments=segments,
        has_document_body=True,
        skills=skills,
        certifications=certs,
    )


def reconstruct(parsed: ParsedResume) -> str:
    return parsed.reconstruct()


def _raw(source: str, start: int, end: int, name: str | None, error: str | None = None) -> Segment:
    return Segment(kind="raw", name=name, start=start, end=end, text=source[start:end], error=error)


# --------------------------------------------------------------------------
# Section parsing
# --------------------------------------------------------------------------

_HEADING_ARITY = {
    "resumeSubheading": 4,
    "resumeSubSubheading": 2,
    "resumeProjectHeading": 2,
    "cventry": 6,
    "subsection": 1,
    "subsection*": 1,
}
_TOKEN_RE = re.compile(
    r"\\(resumeSubheading|resumeSubSubheading|resumeProjectHeading|cventry"
    r"|subsection\*?|resumeItem|item)(?![a-zA-Z])"
)
_ITEM_STOP_RE = re.compile(
    r"\\(?:item|resumeItem|begin|end|resumeSubheading|resumeSubSubheading|resumeProjectHeading"
    r"|cventry|subsection\*?|section\*?|[a-zA-Z]*List(?:Start|End))(?![a-zA-Z])"
)


def _parse_section(source: str, masked: str, start: int, cmd: int, end: int) -> Segment:
    text = source[start:end]
    try:
        title, section_type, content = _parse_section_content(source, masked, cmd, end)
    except Exception as exc:  # any failure -> pass the section through untouched
        return Segment(
            kind="raw",
            name=_try_title(source, masked, cmd, end),
            start=start,
            end=end,
            text=text,
            error=f"{type(exc).__name__}: {exc}",
        )
    return Segment(
        kind="section",
        name=title,
        section_type=section_type,
        start=start,
        end=end,
        text=text,
        content=content,
    )


def _title_group(masked: str, cmd: int, end: int) -> _Group:
    m = _SECTION_CMD_RE.match(masked, cmd)
    assert m is not None
    group = _read_group(masked, _skip_optional(masked, m.end(), end), end)
    if group is None:
        raise _ParseError("section title is not a closed {...} group")
    return group


def _try_title(source: str, masked: str, cmd: int, end: int) -> str | None:
    try:
        g = _title_group(masked, cmd, end)
    except _ParseError:
        return None
    return _inline(source[g.start : g.end]) or None


def _parse_section_content(
    source: str, masked: str, cmd: int, end: int
) -> tuple[str, SectionType, SectionContent]:
    tg = _title_group(masked, cmd, end)
    title = _inline(source[tg.start : tg.end])
    if not _balanced(masked, cmd, end):
        raise _ParseError("unbalanced braces in section")

    section_type = classify_section(title)
    is_cert_section = _CERT_TITLE_RE.search(title) is not None
    body = tg.after
    content = SectionContent(title=title, text=latex_to_plain(source[body:end]))

    current: Entry | None = None
    for tok in _TOKEN_RE.finditer(masked, body, end):
        name = tok.group(1)
        if name in _HEADING_ARITY:
            args = _read_args(masked, tok.end(), end, _HEADING_ARITY[name])
            values = [_inline(source[g.start : g.end]) for g in args]
            raw_args = [source[g.start : g.end] for g in args]
            current = _make_entry(name, values, raw_args, current, tok.start(), args[-1].after)
            content.entries.append(current)
            continue

        if name == "resumeItem":
            g = _read_group(masked, tok.end(), end)
            if g is None:
                raise _ParseError("\\resumeItem without a {...} argument")
            span = (g.start, g.end)
        else:  # \item
            if section_type == "skills":
                continue  # skills lines are parsed from text below
            span = _item_span(masked, tok.end(), end)

        bullet = _make_bullet(source, *span)
        if bullet is not None:
            (current.bullets if current else content.bullets).append(bullet)

    if section_type == "skills":
        for group in _parse_skill_lines(content.text):
            if group.category and _CERT_TITLE_RE.search(group.category):
                content.certifications.extend(group.skills)
            else:
                content.skill_groups.append(group)

    if is_cert_section:
        bullets = content.all_bullets
        content.certifications = [b.text for b in bullets] if bullets else content.text.splitlines()

    return title, section_type, content


def _item_span(masked: str, after: int, limit: int) -> tuple[int, int]:
    """Extent of an ``\\item``'s text: up to the next item/environment
    boundary, or the closing brace of the enclosing group."""
    i = _skip_optional(masked, after, limit)
    nxt = _ITEM_STOP_RE.search(masked, i, limit)
    stop = nxt.start() if nxt else limit
    depth = 0
    for k in range(i, stop):
        if masked[k] == "{":
            depth += 1
        elif masked[k] == "}":
            depth -= 1
            if depth < 0:
                return i, k
    return i, stop


def _make_bullet(source: str, start: int, end: int) -> Bullet | None:
    while start < end and source[start].isspace():
        start += 1
    while end > start and source[end - 1].isspace():
        end -= 1
    latex = source[start:end]
    text = _inline(latex)
    if not text:
        return None
    return Bullet(text=text, latex=latex, start=start, end=end, metrics=extract_metrics(text))


def _make_entry(
    kind: str,
    values: list[str],
    raw_args: list[str],
    previous: Entry | None,
    start: int,
    end: int,
) -> Entry:
    title = org = location = date_text = None
    technologies: list[str] = []

    if kind == "resumeSubheading":
        # Jake's layout: {top-left}{top-right}{bottom-left}{bottom-right}.
        # Experience puts dates top-right; education puts them bottom-right.
        if _is_date_text(values[3]) and not _is_date_text(values[1]):
            org, location, title, date_text = values
        else:
            title, date_text, org, location = values
    elif kind == "resumeSubSubheading":
        title, date_text = values
        org = previous.organization if previous else None
    elif kind == "resumeProjectHeading":
        name, _, tech = values[0].partition("|")
        title = name.strip()
        technologies = _split_items(tech)
        date_text = values[1]
    elif kind == "cventry":
        date_text, title, org, location = values[:4]
    else:  # \subsection{Title, Org \hfill Dates}
        left_raw, _, right_raw = raw_args[0].partition("\\hfill")
        left, right = _inline(left_raw), _inline(right_raw)
        found = parse_dates(right) or parse_dates(left)
        if found and found.text in left:
            left = left.replace(found.text, " ").strip(" ,|–-")
        date_text = found.text if found else None
        title, org, location = _split_title_org(left)

    dates = parse_dates(date_text) if date_text else None
    return Entry(
        title=title or None,
        organization=org or None,
        location=location or None,
        dates=(dates.text if dates else date_text) or None,
        start_date=dates.start if dates else None,
        end_date=dates.end if dates else None,
        technologies=technologies,
        start=start,
        end=end,
    )


def _split_title_org(text: str) -> tuple[str | None, str | None, str | None]:
    for sep in (" at ", " | ", " – ", " — "):
        if sep in text:
            title, _, rest = text.partition(sep)
            break
    else:
        title, _, rest = text.partition(", ")
    org, _, location = rest.partition(", ")
    return title.strip() or None, org.strip() or None, location.strip() or None


def _split_items(text: str) -> list[str]:
    """Split a skill list on , ; | or • outside parentheses."""
    items, buf, depth = [], [], 0
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(depth - 1, 0)
        if depth == 0 and ch in ",;|•":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return [item.strip().rstrip(".").strip() for item in items if item.strip(" .")]


def _parse_skill_lines(text: str) -> list[SkillGroup]:
    groups = []
    for line in text.splitlines():
        category, sep, rest = line.partition(":")
        if sep and category.strip() and len(category) <= 40 and "," not in category:
            group = SkillGroup(category=category.strip(), skills=_split_items(rest))
        else:
            group = SkillGroup(category=None, skills=_split_items(line))
        if group.skills:
            groups.append(group)
    return groups

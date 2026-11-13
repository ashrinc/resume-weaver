"""Apply the model's bullet rewrites to the original resume, enforcing
content preservation (M11.1/M11.2). The model's reply is never trusted.

Three steps, so the async semantic verifier (``verify.py``) can sit between
them: ``judge()`` (Layer A on every bullet) -> verification of the surviving
rewrites -> ``finalize()`` (splice, final check, report).
``apply_tailoring()`` runs judge + finalize without verification.


* Every bullet keeps its original slot in the LaTeX: a rewrite replaces only
  the bullet's content span, so headings, dates, macros and environments are
  untouched by construction. Reordering moves contents between the slots of
  one entry, never across entries.
* Missing ids: the original bullet is restored in its original position.
* Unknown or duplicate ids: discarded.
* A rewrite is reverted to the original if it fails a Layer A guard
  (``guards.py``), absorbs another bullet (a merge), or fails Layer B.
* A skills section is replaced only if it keeps every original skill and adds
  nothing the resume doesn't already mention.
* Final check: bullets per entry and the number of skills are compared before
  and after; a section that fails is reverted whole.

The changes summary is built here from the per-bullet outcome, not from what
the model says it did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.latex.models import ParsedResume, Segment
from app.latex.parser import latex_to_plain, mask_latex, parse_resume
from app.latex.units import BulletUnit, EntryUnit, ResumeUnits, extract_units
from app.latex.validator import validate_latex
from app.llm.models import BulletRewrite, TailoringResponse
from app.llm.prompts import VerifyItem
from app.tailor import guards
from app.tailor.models import BulletChange, ContentCheck, EntryBulletCount

__all__ = [
    "AppliedTailoring",
    "Judgement",
    "apply_tailoring",
    "filter_model_notes",
    "finalize",
    "judge",
    "resume_body_text",
]

_SECTION_START_RE = re.compile(r"\\section\*?(?![a-zA-Z])")
_PREAMBLE_LEVEL_RE = re.compile(
    r"\\(?:documentclass|usepackage|newcommand|renewcommand|providecommand|def)(?![a-zA-Z])"
    r"|\\(?:begin|end)\s*\{document\}"
)


@dataclass
class AppliedTailoring:
    latex: str
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    bullet_changes: list[BulletChange] = field(default_factory=list)
    check: ContentCheck | None = None


@dataclass
class _Outcome:
    unit: BulletUnit
    latex: str  # what ends up in the document
    status: str  # "unchanged" | "reworded" | "reverted" | "restored"
    reason: str | None = None  # revert reason (rule-named)
    requested: int | None = None  # requested 1-based slot within the entry
    proposed: str | None = None  # the model's rewrite (LaTeX), if any
    model_reason: str = ""
    concepts: frozenset[str] = frozenset()  # practices only the verifier can accept


@dataclass
class Judgement:
    """Layer A verdicts for every bullet, before splicing."""

    resume: ParsedResume
    response: TailoringResponse
    units: ResumeUnits
    ctx: guards.ResumeContext
    outcomes: dict[str, list[_Outcome]]  # entry key -> outcomes in original order
    unknown: list[str]
    duplicates: list[str]
    verification: str = "not_needed"  # "ran" | "unavailable" | "not_needed"
    semantic_reverts: int = 0
    warnings: list[str] = field(default_factory=list)

    def reworded(self) -> list[_Outcome]:
        return [o for outs in self.outcomes.values() for o in outs if o.status == "reworded"]

    def pending_verification(self) -> list[VerifyItem]:
        return [
            VerifyItem(id=o.unit.id, original=o.unit.text, rewrite=guards.plain(o.latex))
            for o in self.reworded()
        ]

    def revert(self, bullet_id: str, reason: str) -> None:
        for o in self.reworded():
            if o.unit.id == bullet_id:
                o.status, o.latex, o.reason = "reverted", o.unit.latex, reason


def _norm(latex: str) -> str:
    return " ".join(latex.split())


def resume_body_text(resume: ParsedResume) -> str:
    """Plain text of the resume's sections, parsed or failed: no preamble, no
    contact header, no \\end{document}. What the verifier sees and quotes, and
    what Layer A checks against (so a phone number never counts as a metric)."""
    body = "\n".join(s.text for s in resume.segments if s.kind == "section" or s.error)
    return " ".join(latex_to_plain(body).split())


def _structural_errors(latex: str) -> int:
    return sum(1 for e in validate_latex(latex).errors if not e.code.startswith("missing_"))


def _section_problem(tailored: str, original: str) -> str | None:
    if not tailored.strip():
        return "was empty"
    masked = mask_latex(tailored)
    if not _SECTION_START_RE.match(masked.lstrip()):
        return "did not start with a \\section command"
    if _PREAMBLE_LEVEL_RE.search(masked):
        return "contained preamble-level commands"
    if _structural_errors(tailored) > _structural_errors(original):
        return "had unbalanced braces or environments"
    return None


def _collect(
    units: ResumeUnits, response: TailoringResponse
) -> tuple[dict[str, BulletRewrite], list[str], list[str]]:
    known = units.bullets
    accepted: dict[str, BulletRewrite] = {}
    unknown: list[str] = []
    duplicates: list[str] = []
    for rewrite in response.bullets:
        bid = rewrite.id.strip()
        if bid not in known:
            unknown.append(bid)
        elif bid in accepted:
            duplicates.append(bid)
        else:
            accepted[bid] = rewrite
    return accepted, unknown, duplicates


def _judge_entry(
    entry: EntryUnit,
    accepted: dict[str, BulletRewrite],
    ctx: guards.ResumeContext,
) -> list[_Outcome]:
    outcomes: list[_Outcome] = []
    for unit in entry.bullets:
        rewrite = accepted.get(unit.id)
        if rewrite is None:
            outcomes.append(_Outcome(unit, unit.latex, "restored", "the model omitted it"))
            continue
        new = rewrite.text.strip()
        if _norm(new) == _norm(unit.latex):
            outcome = _Outcome(unit, unit.latex, "unchanged")
        else:
            problem = guards.bullet_problem(unit.latex, new, ctx)
            if problem is None:
                for other in entry.bullets:
                    if other.id != unit.id and guards.merged_into(
                        guards.plain(new), unit.text, other.text
                    ):
                        problem = f"merge: absorbed the content of {other.id}"
                        break
            outcome = (
                _Outcome(unit, unit.latex, "reverted", problem)
                if problem
                else _Outcome(unit, new, "reworded")
            )
            outcome.proposed = new
            if not problem:
                outcome.concepts = frozenset(guards.unverified_concepts(new, ctx))
        outcome.model_reason = rewrite.reason.strip()
        pos = rewrite.position
        if isinstance(pos, int) and 1 <= pos <= len(entry.bullets):
            outcome.requested = pos
        outcomes.append(outcome)
    return outcomes


def _order(outcomes: list[_Outcome]) -> list[_Outcome]:
    """Final order within one entry. Restored bullets keep their original slot;
    requested positions are honoured where the slot is free (first come, in
    original order); everything else fills the remaining slots in original
    order."""
    slots: list[_Outcome | None] = [None] * len(outcomes)
    rest: list[_Outcome] = []
    for o in outcomes:
        if o.status == "restored":
            slots[o.unit.position - 1] = o
    for o in sorted(outcomes, key=lambda o: (o.requested or 0, o.unit.position)):
        if o.status == "restored":
            continue
        if o.requested is not None and slots[o.requested - 1] is None:
            slots[o.requested - 1] = o
        else:
            rest.append(o)
    rest.sort(key=lambda o: o.unit.position)
    free = iter(rest)
    return [o if o is not None else next(free) for o in slots]


def _section_counts(text: str) -> tuple[list[int], int] | None:
    """Bullets per entry (loose bullets first) and skill count of one section."""
    doc = parse_resume("\\begin{document}\n" + text + "\n\\end{document}\n")
    if len(doc.sections) != 1 or doc.sections[0].content is None:
        return None
    content = doc.sections[0].content
    per_entry = [len(content.bullets)] + [len(e.bullets) for e in content.entries]
    skills = sum(len(g.skills) for g in content.skill_groups)
    return per_entry, skills


def _splice(text: str, offset: int, edits: list[tuple[int, int, str]]) -> str:
    for start, end, new in sorted(edits, reverse=True):
        text = text[: start - offset] + new + text[end - offset :]
    return text


def judge(resume: ParsedResume, response: TailoringResponse) -> Judgement:
    """Layer A on every bullet (no splicing yet)."""
    units = extract_units(resume)
    ctx = guards.ResumeContext.from_text(resume_body_text(resume))
    accepted, unknown, duplicates = _collect(units, response)
    outcomes = {e.key: _judge_entry(e, accepted, ctx) for e in units.entries}
    return Judgement(resume, response, units, ctx, outcomes, unknown, duplicates)


def apply_tailoring(resume: ParsedResume, response: TailoringResponse) -> AppliedTailoring:
    """Layer A only (no semantic verification)."""
    return finalize(judge(resume, response))


def finalize(j: Judgement) -> AppliedTailoring:
    resume, units, response = j.resume, j.units, j.response
    out = AppliedTailoring(latex="", warnings=list(j.warnings))
    unknown, duplicates = j.unknown, j.duplicates

    # 1. Order each entry; plan the splices (slot -> new content).
    edits: dict[int, list[tuple[int, int, str]]] = {}
    outcomes_by_entry: list[tuple[EntryUnit, list[_Outcome], bool]] = []
    for entry in units.entries:
        ordered = _order(j.outcomes[entry.key])
        reordered = [o.unit.id for o in ordered] != [b.id for b in entry.bullets]
        for slot, outcome in zip(entry.bullets, ordered, strict=True):
            if outcome.latex != slot.latex:
                edits.setdefault(entry.section_index, []).append(
                    (slot.start, slot.end, outcome.latex)
                )
        outcomes_by_entry.append((entry, ordered, reordered))

    # 2. Build each segment's new text.
    texts = [seg.text for seg in resume.segments]
    for index, seg_edits in edits.items():
        seg = resume.segments[index]
        texts[index] = _splice(seg.text, seg.start, seg_edits)

    skills_changes, skills_warnings = _apply_skills(resume, units, response, texts, j.ctx)

    # 3. Final check: bullets per entry equal, skills not fewer. Never trust the splice.
    reverted_sections: set[int] = set()
    skills_before = skills_after = 0
    final_counts: dict[int, list[int]] = {}
    for index, seg in enumerate(resume.segments):
        if seg.kind != "section":
            continue
        before = _section_counts(seg.text)
        after = _section_counts(texts[index]) if texts[index] != seg.text else before
        if before is None:
            continue
        if after is None or after[0] != before[0] or after[1] < before[1]:
            texts[index] = seg.text
            reverted_sections.add(index)
            out.warnings.append(
                f"Kept the original '{seg.name}' section: the tailored version failed the "
                "final content check (bullet or skill count changed)."
            )
            after = before
        skills_before += before[1]
        skills_after += after[1]
        final_counts[index] = after[0]

    # 4. Report.
    _report(out, outcomes_by_entry, reverted_sections, unknown, duplicates)
    out.changes.extend(skills_changes)
    out.warnings.extend(skills_warnings)
    out.latex = resume.preamble + "".join(texts)
    out.check = ContentCheck(
        entries=_entry_counts(resume, units, final_counts),
        reworded=_count(outcomes_by_entry, "reworded", reverted_sections),
        unchanged=_count(outcomes_by_entry, "unchanged", reverted_sections),
        reverted=_count(outcomes_by_entry, "reverted", reverted_sections),
        restored=_count(outcomes_by_entry, "restored", reverted_sections),
        discarded=len(unknown) + len(duplicates),
        reordered_entries=sum(
            1 for e, _, r in outcomes_by_entry if r and e.section_index not in reverted_sections
        ),
        skills_before=skills_before,
        skills_after=skills_after,
        verification=j.verification,  # type: ignore[arg-type]
        semantic_reverts=j.semantic_reverts,
    )
    return out


def _entry_counts(
    resume: ParsedResume, units: ResumeUnits, final_counts: dict[int, list[int]]
) -> list[EntryBulletCount]:
    """Bullets per entry before (from the units) and after (from re-parsing the
    final section text). Units skip entries without bullets, so they map onto
    the parsed groups that have bullets, in order."""
    result: list[EntryBulletCount] = []
    by_section: dict[int, list[EntryUnit]] = {}
    for entry in units.entries:
        by_section.setdefault(entry.section_index, []).append(entry)
    for index, entries in by_section.items():
        before = _section_counts(resume.segments[index].text)
        after = final_counts.get(index)
        groups = [i for i, n in enumerate(before[0]) if n] if before else []
        for k, entry in enumerate(entries):
            if after is not None and len(groups) == len(entries):
                n_after = after[groups[k]]
            else:  # paragraph section: one unit, spliced in place
                n_after = len(entry.bullets)
            result.append(
                EntryBulletCount(
                    entry_id=entry.key, label=entry.label, before=len(entry.bullets), after=n_after
                )
            )
    return result


def _count(
    outcomes_by_entry: list[tuple[EntryUnit, list[_Outcome], bool]],
    status: str,
    reverted_sections: set[int],
) -> int:
    return sum(
        1
        for entry, ordered, _ in outcomes_by_entry
        if entry.section_index not in reverted_sections
        for o in ordered
        if o.status == status
    )


def _report(
    out: AppliedTailoring,
    outcomes_by_entry: list[tuple[EntryUnit, list[_Outcome], bool]],
    reverted_sections: set[int],
    unknown: list[str],
    duplicates: list[str],
) -> None:
    restored: list[str] = []
    unchanged = 0
    for entry, ordered, reordered in outcomes_by_entry:
        if entry.section_index in reverted_sections:
            continue
        for o in sorted(ordered, key=lambda o: o.unit.position):
            where = f"{o.unit.id} ({entry.label})"
            why = o.model_reason
            if o.status == "reworded":
                out.changes.append(f"Reworded {where}" + (f": {why}" if why else ""))
            elif o.status == "unchanged":
                unchanged += 1
            elif o.status == "reverted":
                out.changes.append(f"Reverted {where} to the original ({o.reason})")
                out.warnings.append(f"Reverted {where} to the original ({o.reason}).")
            elif o.status == "restored":
                out.changes.append(f"Restored {where}: the model omitted it")
                restored.append(o.unit.id)
            out.bullet_changes.append(
                BulletChange(
                    id=o.unit.id,
                    label=entry.label,
                    status=o.status,  # type: ignore[arg-type]
                    reason=o.reason if o.status in ("reverted", "restored") else why,
                    model_reason=why,
                    before=o.unit.text,
                    after=guards.plain(o.latex) if o.status == "reworded" else None,
                    proposed=guards.plain(o.proposed)
                    if o.status == "reverted" and o.proposed
                    else None,
                )
            )
        if reordered:
            order = ", ".join(o.unit.id.split(".", 1)[1] for o in ordered)
            out.changes.append(f"Reordered bullets in {entry.label} (now {order})")
    if unchanged:
        out.changes.append(f"Left {unchanged} bullet{'s' if unchanged != 1 else ''} unchanged")
    if restored:
        n = len(restored)
        out.warnings.insert(
            0,
            f"The model dropped {n} bullet{'s' if n != 1 else ''}; originals restored "
            f"({', '.join(restored)}).",
        )
    if unknown:
        out.warnings.append(
            f"Discarded {len(unknown)} bullet(s) with unknown ids from the model "
            f"({', '.join(sorted(set(unknown)))})."
        )
    if duplicates:
        out.warnings.append(
            f"Discarded {len(duplicates)} duplicate bullet rewrite(s) "
            f"({', '.join(sorted(set(duplicates)))}); the first was used."
        )


def _apply_skills(
    resume: ParsedResume,
    units: ResumeUnits,
    response: TailoringResponse,
    texts: list[str],
    ctx: guards.ResumeContext,
) -> tuple[list[str], list[str]]:
    changes: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for tailored in response.skills_sections:
        sid = tailored.section_id.strip()
        if sid not in units.skills_section_ids:
            warnings.append(f"Ignored a tailored section with unknown id '{sid}'.")
            continue
        if sid in seen:
            warnings.append(f"Ignored a duplicate tailored version of section '{sid}'.")
            continue
        seen.add(sid)
        index = int(sid[1:])
        seg: Segment = resume.segments[index]
        if _norm(tailored.latex) == _norm(seg.text):
            continue
        problem = _section_problem(tailored.latex, seg.text)
        if problem is None:
            problem = guards.skills_problem(seg.text, tailored.latex, ctx)
        if problem:
            changes.append(f"Kept the original '{seg.name}' section: {problem}")
            warnings.append(
                f"Kept the original '{seg.name}' section: the tailored version {problem}."
            )
            continue
        trailing = seg.text[len(seg.text.rstrip()) :] or "\n\n"
        texts[index] = tailored.latex.strip() + trailing
        changes.append(f"Reorganized the '{seg.name}' section (all original skills kept)")
    return changes, warnings


_PRESERVATION_CLAIM_RE = re.compile(
    r"\b(?:preserv\w*|retain\w*|kept|keeps?|keeping|maintain\w*|intact|unchanged|accurate\w*"
    r"|truthful\w*|no\s+(?:new\s+)?(?:facts?|claims?|content|information)|nothing)\b",
    re.IGNORECASE,
)
_FACT_WORD_RE = re.compile(
    r"\b(?:facts?|factual\w*|details?|content|information|claims?|metrics?|numbers?"
    r"|accura\w*|truth\w*|fabricat\w*|invent\w*)\b",
    re.IGNORECASE,
)


def filter_model_notes(notes: list[str]) -> list[str]:
    """The model's notes minus any claim about preserving facts (the backend
    checks that itself), deduplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for note in notes:
        note = note.strip()
        key = note.casefold()
        if not note or key in seen:
            continue
        if _PRESERVATION_CLAIM_RE.search(note) and _FACT_WORD_RE.search(note):
            continue
        seen.add(key)
        out.append(note)
    return out

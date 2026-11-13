"""Prompt templates and builders for Gemini resume tailoring (PRD FR-05/FR-06).

Templates live here, not inline in the client, so they can be iterated on
independently. Bump ``PROMPT_VERSION`` whenever a template changes; M6 uses it
in the response cache key.

What the model receives (tailor-v4, M11.2; bullet ids since tailor-v3):
* every bullet of the parsed sections with a stable id (``exp1.b3``, see
  ``app/latex/units.py``), grouped by entry, with the entry heading as context;
* skills sections as whole LaTeX, with ids ``s<segment index>``;
* categorized JD requirements with weights, and a keyword priority list;
* the requirement matching plan (which bullets support which requirement);
* the goal (improve relevant bullets) first, then a short list of hard
  limits the backend enforces anyway, and the output format. No page limit.

The verifier prompt (verify-v1) gets the plain text of the original resume
body and {id, original, rewrite} for changed bullets, and must quote exact
evidence from the original.

What it never receives: the LaTeX preamble, the raw header/contact block,
entry headings as editable text, or sections that failed to parse. The
backend passes those through and reattaches the preamble itself.

Templates use ``string.Template`` ($placeholders) because LaTeX and JSON are
full of braces that ``str.format`` would misinterpret.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from string import Template

from app.jd.models import JDRequirements
from app.latex.models import ParsedResume
from app.latex.parser import mask_latex
from app.latex.units import ResumeUnits, extract_units, section_id
from app.matching.models import RequirementMatch

PROMPT_VERSION = "tailor-v4"
VERIFY_PROMPT_VERSION = "verify-v1"

SYSTEM_PROMPT = """\
You are an expert technical resume writer. Your job is to make this resume a \
stronger, clearer match for one specific job by rewriting the bullets that \
matter for it, the way a skilled recruiter-facing editor would.

You work from facts already in the resume: you improve how they are said, \
never what they claim. Every bullet stays; nothing is merged or invented.

Return only JSON in the specified format. Do not wrap it in markdown code \
fences and do not add any commentary before or after it."""

TAILORING_TEMPLATE = Template("""\
## Goal
Improve the bullets that are relevant to this job. A good rewrite:
- leads with a strong, accurate action verb;
- puts the technical implementation first, then the outcome or metric;
- uses the job description's own terminology (each skill's "jd_wording") \
wherever the original bullet's facts support it, for example "RESTful APIs" \
for a bullet about REST endpoints, or "query optimization" for "tuned slow \
queries";
- keeps every metric exactly as written.

Use the "matching_plan": for each JD requirement it lists the bullets that \
support it ("target_bullets") and how well. Strengthen "partially_matched" \
targets first, then make "matched" ones state the JD's wording clearly. \
Aim to rewrite most relevant bullets; do not play it safe. Leave a bullet \
unchanged only if it is already strong and JD-aligned, or irrelevant to this \
job, and say which in its "reason".

You may also reorder bullets within an entry (most relevant first) and \
reorder or regroup the skills section.

## Hard limits (the backend checks every rewrite and reverts violations)
1. Never delete a bullet or merge two bullets. Return every id exactly once.
2. No new numbers or metrics, and keep every number of the original bullet.
3. No tools, technologies or platforms the resume does not mention. Do not \
replace named tools with vaguer scope ("Vercel and Render" must not become \
"frontend and backend deployments").
4. No inflated scope or certainty: "prevent" is not "eliminate", "reduce" is \
not "eliminate", "some" is not "all", "contributed to" or "assisted" is not \
"led" or "owned".
5. Skills section: never drop a skill; add a skill only if the resume already \
mentions it (or a more specific tool for it, e.g. PostgreSQL for "relational \
databases"). Nothing from "not_in_resume" may be added anywhere.

## LaTeX
- A bullet's "text" is only its content: no \\item, \\resumeItem, \\begin, \
\\end or surrounding braces. Keep the original's bold style (\\textbf only on \
the kinds of terms the original bolds) and only use commands from \
"allowed_latex".
- Escape special characters: write "40\\%", not "40%"; also \\&, \\$$, \\#, \\_.
- This is JSON: escape every backslash, so \\textbf is written as "\\\\textbf".

## Output format
One JSON object with exactly these keys:
- "bullets": one object per input bullet id: {"id", "text": the bullet \
content, "changed": true or false, "reason": one short sentence naming the \
requirement the rewrite serves, or why it was left unchanged, "position": \
optional new 1-based position within its entry}.
- "skills_sections": one object per input skills section: {"section_id", \
"latex": the complete section starting with its \\section command}.
- "ats_analysis": {"score": integer 0-100 for how well the tailored resume \
truthfully matches the job, "reasoning": 2-4 sentences, "matched_keywords", \
"missing_keywords" (include every "not_in_resume" skill), \
"improvement_suggestions": things the candidate could do, never "add X" for \
something they lack}.
- "notes": up to 5 short notes on your strategy. Do not claim facts were \
preserved; the backend checks that.

## Job requirements (with responsibilities)
$job_json

## Keyword priority list (most important first)
$keywords_json

## Matching plan (requirement -> supporting bullets)
$plan_json

## not_in_resume (JD skills the resume does not mention: never add)
$missing_json

## Allowed LaTeX
$allowed_latex_json

## Resume entries and their bullets
$entries_json

## Skills sections
$skills_json
""")

VERIFY_SYSTEM_PROMPT = """\
You are a strict fact checker for resumes. For each rewritten bullet you \
decide whether everything it claims is supported by the ORIGINAL resume. You \
quote the original resume exactly as evidence. Rewording, reordering and using \
standard industry terms for the same thing are fine; new facts are not.

Return only JSON in the specified format, with no markdown fences."""

VERIFY_TEMPLATE = Template("""\
Check each rewritten bullet against the original resume below.

For each item return:
- "verdict": "supported" if every claim in the rewrite is backed by the \
original resume (same facts, possibly reworded, or a standard term for the \
same thing, e.g. "relational database" for PostgreSQL); "partially_supported" \
if part of it is not backed; "unsupported" if its main claim is not backed.
- "evidence": an EXACT quote copied from the original resume text below that \
supports the rewrite (a phrase of at least a few words). Never paraphrase it.
- "problem": what is not supported (empty if supported).
- "wording_only": for partially_supported, true only if the unsupported part \
is phrasing or emphasis, false if it adds a fact, tool, number, scope, \
ownership (e.g. "led"), certainty (e.g. "eliminated") or outcome.

Unsupported examples: a tool or metric not in the resume; "led" for work \
the resume says the candidate helped with; "eliminated" for "prevented"; \
claiming work for a different employer or project; inflating "some" to "all".

## Output format
{"results": [{"id", "verdict", "evidence", "problem", "wording_only"}, ...]} \
with exactly one result per item.

## Original resume (body text)
$resume_text

## Items to check
$items_json
""")

REPAIR_TEMPLATE = Template("""\
Your previous reply could not be used because it was not valid JSON in the \
required format.

Problem: $error

Return the same content again as a single valid JSON object with exactly the \
keys specified before (for tailoring: "bullets", "skills_sections", \
"ats_analysis" and "notes", with every bullet id exactly once; for \
verification: "results"). \
Escape every backslash inside JSON strings ("\\\\textbf"). Do not use markdown \
code fences and do not add commentary.

Your previous reply:
$previous
""")

MAX_REPAIR_ECHO_CHARS = 30_000


@dataclass(frozen=True)
class TailoringPrompt:
    system: str
    user: str
    bullet_ids: list[str]
    skills_section_ids: list[str]


def keyword_priority(jd: JDRequirements) -> list[str]:
    """Mandatory, then preferred, then soft skills; within each group the
    most frequently mentioned first (ties keep JD order)."""
    groups = (jd.mandatory_skills, jd.preferred_skills, jd.soft_skills)
    return [s.name for group in groups for s in sorted(group, key=lambda s: -s.occurrences)]


def _plan_payload(plan: list[RequirementMatch]) -> list[dict[str, object]]:
    """Requirement -> supporting bullets, partial matches first (most to gain)."""
    order = {"partially_matched": 0, "matched": 1, "missing": 2}
    out: list[dict[str, object]] = []
    for m in sorted(plan, key=lambda m: order[m.status]):
        if m.status == "missing":
            how = "no support in the resume: do not add it"
        elif m.status == "partially_matched":
            how = "supported but not stated: strengthen the target bullets using the JD wording"
        else:
            how = "covered: make sure the target bullets state it clearly"
        out.append(
            {
                "requirement": m.requirement,
                "kind": m.kind,
                "status": m.status,
                "target_bullets": m.bullet_ids,
                "how": how,
            }
        )
    return out


def _entries_payload(units: ResumeUnits) -> list[dict[str, object]]:
    return [
        {
            "entry_id": e.key,
            "section": e.section_name,
            "heading": e.heading,
            "bullets": [{"id": b.id, "latex": b.latex} for b in e.bullets],
        }
        for e in units.entries
    ]


def _skills_payload(resume: ParsedResume, units: ResumeUnits) -> list[dict[str, str | None]]:
    out: list[dict[str, str | None]] = []
    for sid in units.skills_section_ids:
        seg = resume.segments[int(sid[1:])]
        out.append({"id": sid, "title": seg.name, "latex": seg.text.strip()})
    return out


def _allowed_latex(latex_blocks: list[str]) -> dict[str, list[str]]:
    masked = mask_latex("\n".join(latex_blocks))
    environments = sorted(set(re.findall(r"\\(?:begin|end)\s*\{([^{}]+)\}", masked)))
    commands = sorted(set(re.findall(r"\\([A-Za-z]+\*?)", masked)) - {"begin", "end"})
    return {
        "commands": [f"\\{c}" for c in commands],
        "environments": environments,
        "special_characters": ["\\%", "\\&", "\\$", "\\#", "\\_", "\\\\"],
    }


def _job_payload(jd: JDRequirements) -> dict:
    def skills(group):  # type: ignore[no-untyped-def]
        return [{"name": s.name, "weight": s.weight, "jd_wording": s.matched_terms} for s in group]

    return {
        "title": jd.title,
        "seniority": jd.seniority,
        "years_of_experience": {
            "min": jd.experience.min_years,
            "max": jd.experience.max_years,
        },
        "mandatory_skills": skills(jd.mandatory_skills),
        "preferred_skills": skills(jd.preferred_skills),
        "soft_skills": skills(jd.soft_skills),
        "responsibilities": jd.responsibilities,
        "certifications": [{"name": c.name, "weight": c.weight} for c in jd.certifications],
    }


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def build_tailoring_prompt(
    resume: ParsedResume,
    jd: JDRequirements,
    keywords: list[str] | None = None,
    missing_keywords: list[str] | None = None,
    plan: list[RequirementMatch] | None = None,
) -> TailoringPrompt:
    """Build the system and user prompts. ``keywords`` overrides the default
    priority list. ``missing_keywords`` are JD skills the original resume does
    not mention (the pipeline gets them from scoring the original); the prompt
    forbids introducing them. ``plan`` is the requirement matching of the
    original resume (``app/matching``)."""
    units = extract_units(resume)
    entries = _entries_payload(units)
    skills = _skills_payload(resume, units)
    latex_blocks = [b.latex for e in units.entries for b in e.bullets]
    latex_blocks += [str(s["latex"]) for s in skills]
    user = TAILORING_TEMPLATE.substitute(
        job_json=_dump(_job_payload(jd)),
        keywords_json=_dump(keywords if keywords is not None else keyword_priority(jd)),
        missing_json=_dump(missing_keywords or []),
        plan_json=_dump(_plan_payload(plan or [])),
        allowed_latex_json=_dump(_allowed_latex(latex_blocks)),
        entries_json=_dump(entries),
        skills_json=_dump(skills),
    )
    return TailoringPrompt(
        system=SYSTEM_PROMPT,
        user=user,
        bullet_ids=list(units.bullets),
        skills_section_ids=units.skills_section_ids,
    )


@dataclass(frozen=True)
class VerifyItem:
    id: str
    original: str  # plain text
    rewrite: str  # plain text


def build_verify_prompt(resume_text: str, items: list[VerifyItem]) -> tuple[str, str]:
    """(system, user) for the semantic verifier. ``resume_text`` is the plain
    text of the original resume's body sections: no preamble, no contact
    header."""
    user = VERIFY_TEMPLATE.substitute(
        resume_text=resume_text,
        items_json=_dump(
            [{"id": i.id, "original": i.original, "rewrite": i.rewrite} for i in items]
        ),
    )
    return VERIFY_SYSTEM_PROMPT, user


def build_repair_prompt(previous_output: str, error: str) -> str:
    previous = previous_output
    if len(previous) > MAX_REPAIR_ECHO_CHARS:
        previous = previous[:MAX_REPAIR_ECHO_CHARS] + "\n...[truncated]"
    return REPAIR_TEMPLATE.substitute(error=error, previous=previous)


__all__ = [
    "PROMPT_VERSION",
    "VERIFY_PROMPT_VERSION",
    "VerifyItem",
    "build_verify_prompt",
    "TailoringPrompt",
    "build_repair_prompt",
    "build_tailoring_prompt",
    "keyword_priority",
    "section_id",
]

# PRD: Personal AI Resume Tailor
Version: 2.0 · Nov 12, 2026 · @Venkata sai pavan Athmakuru Status: Ready for Development
Product Type: Personal Web Application Primary User: Individual job seeker
## 1. Product Overview
A personal web application that accepts a LaTeX resume and a target job description,
then generates a refined, ATS-optimized, job-specific LaTeX resume. The backend preparses the LaTeX into structured sections using rule-based extraction, analyzes the JD for
requirements and keywords, sends structured data to Google Gemini (free tier) for
content optimization, and returns complete LaTeX source with a dual-scored ATS
alignment report — one score from Gemini, one from a deterministic programmatic
scorer.
The application also provides a separate archive for storing manually uploaded PDF
resumes against company names.
The LLM is instructed to target a one-page resume.
## 2. Objectives
Generate job-specific, one-page resumes from an existing LaTeX resume.
Target an internal ATS/JD alignment score above 90/100 (dual-scored: LLM +
programmatic).
Improve keyword coverage and relevance to the target JD.
Improve bullet-point quality, clarity, and technical specificity.
Maintain a professional, ATS-friendly resume structure.
Preserve the original LaTeX formatting, template, and macros.
Validate generated LaTeX output for basic syntax correctness.
Maintain a separate archive of tailored PDF resumes by company.
## 3. Functional Requirements
### FR-01: Resume Input
The application must provide an interface to:
# PRD: Personal AI Resume Tailor
Page 1 of 12
Paste LaTeX resume source code into a text editor with syntax highlighting.
Paste the complete job description into a separate text area.
Submit both inputs for processing.
Validate that neither input is empty before submission.
Supported inputs: LaTeX source code and plain-text JD.
### FR-02: Resume Preprocessing (Rule-Based)
Before sending information to the LLM, the backend must perform rule-based extraction
using regex and LaTeX-aware parsing:
Split the LaTeX source into structural elements (preamble, document body, macros,
custom commands) and editable content sections.
Extract named sections: experience, projects, education, skills, summary/objective.
Identify individual bullet points within each section.
Extract employment dates, company names, job titles, and quantified achievements.
Extract the technical skills list and any certifications.
Retain the complete original LaTeX source for reconstruction after optimization.
The parser must handle common LaTeX resume templates (e.g., \section , \subsection ,
\resumeSubheading , custom \begin{itemize} blocks). If a section cannot be reliably
parsed, pass it through unmodified.
### FR-03: Job Description Analysis (Rule-Based + LLM)
Extract and categorize from the JD using a combination of keyword extraction (rulebased) and LLM analysis:
Mandatory technical skills (explicitly required).
Preferred technical skills (nice-to-have).
Key responsibilities and duties.
Experience level requirements (years, seniority).
Domain-specific keywords and terminology.
Important qualifications and certifications.
Relevant soft skills.
Assign importance weights: mandatory skills = high, preferred skills = medium, soft skills
= low. These weights feed into the ATS scoring model (FR-04).
# PRD: Personal AI Resume Tailor
Page 2 of 12
### FR-04: ATS Optimization and Resume Matching
This is the core functionality. The system must compare the structured resume data
against the categorized JD requirements and optimize forATS compatibility.
#### Optimization requirements:
Maximize relevant keyword coverage without keyword stuffing.
Prioritize mandatory skills and qualifications in prominent positions.
Incorporate JD terminology naturally into bullet points.
Align experience bullet points with job responsibilities.
Reorder skills list by JD relevance (mandatory first, then preferred, then remaining).
Prioritize the most relevant projects and experience entries.
Use clear, standard section headings (Experience, Education, Skills, Projects).
PreferATS-readable formatting: avoid graphics, tables, icons, and complex multicolumn layouts that interfere with parsing.
Maintain concise, keyword-rich bullet points.
Target a one-page resume. Instruct the LLM to trim or consolidate content to fit one
page.
#### Dual ATS scoring:
The application calculates alignment scores from two independent sources:
## 1. LLM score — Gemini evaluates the optimized resume against the JD and returns a
score with reasoning. This captures semantic understanding (e.g., "built CI/CD
pipelines" matches "DevOps experience").
## 2. Programmatic score — A deterministic Python scorer calculates a weighted score
based on exact and fuzzy keyword matching.
#### Programmatic scoring model:
Evaluation criterion Weight Method
Mandatory skills coverage 35 Exact + fuzzy match of mandatory skills
against resume text
Preferred skills coverage 15 Exact + fuzzy match of preferred skills
Experience and responsibility
alignment
25 TF-IDF or cosine similarity between JD
responsibilities and resume bullets
# PRD: Personal AI Resume Tailor
Page 3 of 12
Target: both scores above 90/100.
#### Display requirements:
Both scores (LLM and programmatic) with a brief explanation of each.
Mandatory keywords: matched vs. missing.
Preferred keywords: matched vs. missing.
Skill alignment breakdown.
Areas with opportunities for improvement.
The scores represent internal JD alignment and estimated ATS readiness, not a
guaranteed score from any external ATS platform.
### FR-05: Resume Content Optimization
The LLM must:
Rewrite bullet points using strong action verbs (Led, Built, Designed, Optimized,
Reduced, etc.).
Improve technical specificity and clarity (replace vague claims with concrete details).
Emphasize relevant achievements and responsibilities that match JD requirements.
Prioritize relevant experience and projects (reorder sections if beneficial).
Optimize the skills section ordering by JD relevance.
Improve readability and conciseness.
Reduce or remove irrelevant content to maintain a one-page length.
Optimize content for the target role.
The user will manually review the generated content before using the resume. Automated
factual-claim verification is not required.
Evaluation criterion Weight Method
Technical keyword coverage 15 Percentage of JD technical terms found in
resume
ATS structure and readability 10 Rule-based checks: standard headings, no
complex formatting, bullet usage
Total 100
# PRD: Personal AI Resume Tailor
Page 4 of 12
### FR-06: LLM Integration (Google Gemini Free Tier)
Integrate the Google Gemini API (free tier, gemini-2.0-flash or latest available free
model).
Use structured prompts with clear system instructions, output constraints, and
examples.
The LLM receives:
Structured resume sections (from FR-02 preprocessing).
Categorized JD requirements with weights (from FR-03).
Keyword priority list.
ATS optimization instructions.
LaTeX preservation instructions (do not modify preamble, macros, or structural
commands).
One-page length constraint.
Required output format: complete LaTeX source + structured JSON ATS analysis.
Handle API errors (4xx, 5xx), rate limits (429 with retry + backoff), timeouts, and
malformed responses gracefully.
Store the API key in an environment variable ( GEMINI_API_KEY ). Never expose it to
the frontend.
Include a system-level disclosure that resume content is sent to Google’s Gemini API.
### FR-07: LaTeX Output and Validation
The application must:
Display the complete generated LaTeX source in a read-only editor with syntax
highlighting.
Preserve the original template, document class, preamble, macros, and formatting
commands.
Run a basic syntax validation on the output:
Check for unmatched braces {} , brackets [] , and parentheses.
Check for unmatched \begin{} / \end{} environments.
Check for missing required commands ( \documentclass , \begin{document} ,
\end{document} ).
Report validation errors clearly to the user.
Allow users to copy the generated LaTeX source to clipboard.
# PRD: Personal AI Resume Tailor
Page 5 of 12
Allow users to download the generated .tex file.
If validation finds errors, still show the output but display a warning banner listing the
issues.
The application does not compile LaTeX or generate PDFs.
### FR-08: Output Analysis Display
Display the following alongside the generated LaTeX:
LLM ATS alignment score with reasoning summary.
Programmatic ATS alignment score with per-criterion breakdown.
Matched mandatory keywords (highlighted).
Matched preferred keywords.
Missing mandatory keywords (flagged as high priority).
Missing preferred keywords.
Skill alignment summary.
Improvement summary: what changed and why.
### FR-09: Tailored Resume Archive
A separate interface to save manually uploaded PDF resumes.
Users must be able to:
Enter a company name (required).
Enter a job role (optional).
Upload a PDF file (PDF only, max 10 MB).
View a list of previously uploaded resumes sorted by upload date (newest first).
Download stored PDFs.
Delete an archived resume (with confirmation).
#### Archive metadata:
Field Type
ID Integer, auto-increment
Company name String, required
Job role String, optional
# PRD: Personal AI Resume Tailor
Page 6 of 12
PDF files stored in the local filesystem under ./uploads/resumes/ . Metadata stored in
SQLite.
Validation: accept only .pdf files, reject files over 10 MB, validate MIME type.
## 4. Non-Functional Requirements
Performance
Preprocessing (rule-based parsing) must complete in under 2 seconds for a typical 1–
2 page resume.
Display a loading state with progress indication while the LLM generates output.
Handle Gemini API timeouts (set a 60-second timeout; show a clear error if
exceeded).
Avoid unnecessary LLM calls — do not re-call the API if inputs haven’t changed.
Reliability
Handle malformed LaTeX gracefully — if parsing fails for a section, pass it through
unmodified.
Handle incomplete or poorly formatted JDs without crashing.
Handle LLM API failures (network errors, rate limits, malformed responses) without
losing user inputs. User inputs must persist in the UI across errors.
Prevent invalid file uploads (non-PDF, oversized) with clear error messages.
Preserve uploaded PDF files and their metadata across application restarts.
Security and Privacy
Store the Gemini API key in a .env file loaded via environment variables. Never
hardcode it.
Field Type
PDF file path String
Original filename String
File size (bytes) Integer
Upload timestamp DateTime, UTC
# PRD: Personal AI Resume Tailor
Page 7 of 12
Do not expose the API key to the frontend. All LLM calls go through the FastAPI
backend.
Do not log complete resumes or job descriptions to application logs.
Keep personal resume data local wherever possible.
Display a one-time disclosure notice: "Your resume content is sent to the Google
Gemini API for processing."
Maintainability
Separate preprocessing, LLM integration, and scoring into independent modules.
Use clear interfaces between the parser, the Gemini client, and the programmatic
scorer so any can be replaced independently.
Keep LLM prompts in a separate configuration file or constants module for easy
iteration.
## 5. Technology Stack
Component Technology Notes
Frontend Next.js + React TypeScript
Backend Python + FastAPI Async endpoints
LLM Google Gemini API (free tier) gemini-2.0-flash or latest free
model
Resume parsing Python (regex + string parsing) Rule-based LaTeX section
extraction
JD analysis Python (keyword extraction) +
Gemini
Hybrid: rule-based + LLM
Programmatic
scorer
Python TF-IDF / cosine similarity +
keyword matching
LaTeX validation Python (regex-based syntax
checks)
Brace, environment, command
validation
Database SQLite + SQLAlchemy Archive metadata
# PRD: Personal AI Resume Tailor
Page 8 of 12
## 6. Application Screens
### Screen 1: Resume Tailoring
#### Input panel (left):
LaTeX source editor with syntax highlighting.
Job description text area.
"Generate" button (disabled until both fields are non-empty).
Loading indicator with status text during generation.
#### Output panel (right):
Generated LaTeX source editor (read-only, syntax highlighted).
Copy to clipboard button.
Download .tex file button.
LaTeX validation status (pass / warnings with details).
#### Analysis panel (below output):
LLM ATS score with reasoning summary.
Programmatic ATS score with per-criterion breakdown.
Matched keywords (mandatory and preferred, tagged).
Missing keywords (mandatory flagged as high priority).
Improvement summary.
#### Error states:
Empty input validation messages.
API error / timeout message with retry option.
LaTeX validation warning banner.
### Screen 2: Resume Archive
Company name input (required).
Job role input (optional).
Component Technology Notes
File storage Local filesystem
( ./uploads/resumes/ )
PDF archive only
# PRD: Personal AI Resume Tailor
Page 9 of 12
PDF file upload with drag-and-drop (accepts .pdf only, max 10 MB).
Upload button.
Table of archived resumes: company, role, filename, upload date.
Per-row actions: download, delete (with confirmation dialog).
Empty state message when no resumes are archived.
## 7. Acceptance Criteria
## 1. A user can provide valid LaTeX source code and a JD.
## 2. The backend pre-parses the LaTeX into structured sections using rule-based
extraction.
## 3. The backend extracts and categorizes JD requirements with importance weights.
## 4. Structured data is sent to Google Gemini for content optimization.
## 5. The generated resume is tailored to the target JD and targets one page.
## 6. Both LLM and programmatic ATS alignment scores are calculated and displayed.
## 7. Both scores target above 90/100.
## 8. Matched and missing keywords (mandatory and preferred) are displayed.
## 9. The original LaTeX template, preamble, and macros are preserved.
## 10. Generated LaTeX passes basic syntax validation (unmatched braces, environments,
required commands).
## 11. If validation finds issues, a warning is shown but the output is still displayed.
## 12. Generated LaTeX can be copied to clipboard and downloaded as a .tex file.
## 13. The user can manually review and edit the generated content externally.
## 14. A user can upload a PDF (max 10 MB) with a company name to the archive.
## 15. Archived PDFs and metadata persist across application restarts.
## 16. Users can view, download, and delete archived PDFs.
## 17. API errors, rate limits, and timeouts are handled without losing user inputs.
## 18. A disclosure notice about data being sent to Google Gemini is shown to the user.
## 8. Explicit Scope Exclusions
The application will NOT include:
PDF generation or LaTeX compilation.
# PRD: Personal AI Resume Tailor
Page 10 of 12
Automatic factual-claim verification.
Iterative refinement or regeneration (one-shot generation only; user edits externally).
Automatic job applications orATS submission.
Job scraping, job discovery, or LinkedIn/email integration.
User accounts, authentication, or multiple resume profiles.
Cloud file storage (all storage is local).
Subscription, payment, or monetization functionality.
Automated application tracking.
Integration between the tailoring workflow and the archive (they are independent
features).
## 9. Development Ticket
Ticket ID: RT-001 Title: Build Personal AI-Powered ATS Resume Tailoring Application Type:
Full-stack Feature Priority: High
#### Implementation Tasks
Set up Next.js (TypeScript) frontend and FastAPI backend project structure.
Configure SQLite database with SQLAlchemy models for archive metadata.
Implement LaTeX resume input editor with syntax highlighting.
Implement JD input text area with validation.
Build rule-based LaTeX parser: section extraction, bullet-point identification, skills
extraction.
Build JD extraction module: keyword categorization, importance weighting.
Implement resume-to-JD relevance mapping (structured data preparation for LLM).
Integrate Google Gemini API (free tier): structured prompts, error handling, retry with
backoff.
Design and test LLM prompts forATS optimization, one-page targeting, and LaTeX
preservation.
Build programmatic ATS scorer: keyword matching (exact + fuzzy), TF-IDF similarity,
structure checks.
Implement LaTeX syntax validation (brace matching, environment matching, required
commands).
Implement LaTeX output display with syntax highlighting, copy, and download.
# PRD: Personal AI Resume Tailor
Page 11 of 12
#### Definition of Done
All functional requirements and acceptance criteria in PRD v2.0 are satisfied. The
application runs locally. The tailoring workflow produces a valid one-page LaTeX resume
with both ATS scores above 90/100 for test JDs. The archive supports the full
upload/view/download/delete lifecycle.
Display dual ATS scores, matched/missing keywords, and improvement summary.
Implement PDF archive: upload, metadata storage, listing, download, deletion.
Add file validation for PDF uploads (type, size, MIME).
Add loading states, error states, and the Gemini API disclosure notice.
Test with at least 5 different job descriptions across different roles.
# PRD: Personal AI Resume Tailor
Page 12 of 12

"""Render stored analysis results without fetching source or invoking a model."""
import html
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from app.models import Alert, AnalysisRun, Repository
from app.serializers import iso, run_json


def inline(value) -> str:
    """Keep untrusted text within one Markdown line, with no HTML or links."""
    text = " ".join(str(value if value is not None else "").splitlines())
    text = html.escape(text, quote=False)
    # Also escape URL/email punctuation so plain stored URLs cannot introduce
    # automatic links. Entity delimiters stay intact for correct HTML escaping.
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|~:/@=])", r"\\\1", text)


def source_block(snippet: str) -> str:
    """Preserve source verbatim inside a fence that the source cannot close."""
    longest = max((len(match.group()) for match in re.finditer(r"`+", snippet)), default=0)
    fence = "`" * max(3, longest + 1)
    ending = "" if snippet.endswith("\n") else "\n"
    return f"{fence}text\n{snippet}{ending}{fence}"


def identifier(value) -> str:
    """Keep refs, paths and IDs copyable within a safe inline code span."""
    text = " ".join(str(value if value is not None else "").splitlines())
    if not text:
        return "Not recorded"
    longest = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    padding = " " if text.startswith(("`", " ")) or text.endswith(("`", " ")) else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def report_filename(run_id: str) -> str:
    try:
        identifier = UUID(run_id)
    except (ValueError, TypeError, AttributeError):
        # Defensive for imported legacy records; never interpolate arbitrary
        # database text into an attachment header or a filesystem path.
        identifier = uuid5(NAMESPACE_URL, f"splash-analysis:{run_id}")
    return f"splash-analysis-{identifier}.md"


def bounded_note(value, limit=1200):
    if not isinstance(value, str) or not value.strip():
        return ""
    return inline(value[:limit] + (" … truncated" if len(value) > limit else ""))


def ai_notes(explanation) -> list[str]:
    if not isinstance(explanation, dict):
        return []
    notes = []
    if summary := bounded_note(explanation.get("summary")):
        notes.append(summary)
    hypotheses = explanation.get("hypotheses")
    if isinstance(hypotheses, list):
        for hypothesis in hypotheses[:4]:
            if isinstance(hypothesis, dict) and (claim := bounded_note(hypothesis.get("claim"), 600)):
                notes.append(f"- Hypothesis: {claim}")
    recommendations = explanation.get("recommendations")
    if isinstance(recommendations, list):
        for recommendation in recommendations[:4]:
            if note := bounded_note(recommendation, 600):
                notes.append(f"- Suggested follow-up: {note}")
    if not notes:
        return []
    return ["#### AI notes — unverified", "",
            "These stored model notes are advisory and unverified. They do not establish a dependency; "
            "source evidence is listed separately below.", "", *notes, ""]


def render_analysis_report(run: AnalysisRun, repository: Repository, alerts: list[Alert]) -> str:
    metadata = run_json(run, repository)
    rows = ["# Splash analysis report", "", f"## {inline(run.title)}", "",
            f"- Run ID: {identifier(run.id)}",
            f"- Repository: {identifier(repository.full_name)}",
            f"- Kind: {inline(run.kind)}",
            f"- Status: {inline(run.status)}",
            f"- Pull request: {identifier('#' + str(run.pr_number)) if run.pr_number is not None else 'Not associated'}",
            f"- Created: {identifier(iso(run.created_at))}",
            f"- Completed: {identifier(iso(run.completed_at))}",
            f"- Analysis base ref: {identifier(metadata['base_sha'])}",
            f"- Analysis head ref: {identifier(metadata['head_sha'])}",
            f"- Stored findings in this run: {len(alerts)}"]
    metrics, payload = run.metrics or {}, run.payload or {}
    event_base = metrics.get("event_base_sha") or payload.get("base_sha")
    if event_base and event_base != metadata["base_sha"]:
        rows.append(f"- PR target base ref at delivery: {identifier(event_base)}")
    if payload.get("pr_url"):
        rows.append(f"- Pull request URL (stored): {identifier(payload['pr_url'])}")
    rows += ["", "## Scope", "",
             "This report contains the stored results of this specific run within one repository. "
             "Static analysis covers supported source files and recognized dependency patterns; "
             "it does not execute repository code or prove runtime behavior. "
             "No findings means no supported impact was detected, not that every possible change is safe. "
             "Evidence is copied from the run's stored citations, even if its raw snapshots have expired."]
    if repository.is_demo:
        rows += ["", "This is a local fixture. Its demo references identify content snapshots, not GitHub commits."]
    rows += ["", "## Analysis stages", ""]
    stages = run.stages if isinstance(run.stages, list) else []
    for number, stage in enumerate(stages, 1):
        if not isinstance(stage, dict):
            continue
        rows += [f"### {number}. {inline(stage.get('name', 'Stage'))}", "",
                 f"- Status: {inline(stage.get('status', 'Not recorded'))}",
                 f"- Duration (ms): {inline(stage.get('duration_ms', 'Not recorded'))}", "",
                 inline(stage.get("summary", "")), ""]
    if not stages:
        rows += ["No stage summaries were stored.", ""]
    rows += ["## Findings", ""]
    if not alerts:
        rows += ["No findings were recorded for this analysis run.", ""]
    for number, alert in enumerate(alerts, 1):
        rows += [f"### {number}. {inline(alert.title)}", "",
                 f"- Finding ID: {identifier(alert.id)}",
                 f"- Run ID: {identifier(alert.run_id)}",
                 f"- Severity: {inline(alert.severity)}",
                 f"- Confidence: {inline(alert.confidence)}",
                 f"- Evidence status: {inline(alert.evidence_status)}",
                 f"- Finding status at export: {inline(alert.status)}",
                 f"- Change type: {inline(alert.change_type)}",
                 f"- Affected component: {identifier(alert.affected_component)}", "",
                 inline(alert.summary), "", "#### Recommendations", ""]
        recommendations = alert.recommendations if isinstance(alert.recommendations, list) else []
        rows += [f"- {inline(recommendation)}" for recommendation in recommendations]
        if not recommendations:
            rows.append("No recommendations were stored.")
        rows += ["", *ai_notes(alert.ai_explanation), "#### Stored source evidence", ""]
        evidence = alert.evidence if isinstance(alert.evidence, list) else []
        for index, citation in enumerate(evidence, 1):
            if not isinstance(citation, dict):
                continue
            rows += [f"##### Evidence {index}", "",
                     f"- Path: {identifier(citation.get('path'))}",
                     f"- Ref: {identifier(citation.get('commit_sha') or citation.get('sha'))}",
                     f"- Lines: {inline(citation.get('start_line', '?'))}–{inline(citation.get('end_line', '?'))}",
                     f"- Role: {inline(citation.get('role', 'Not recorded'))}"]
            if citation.get("source_url"):
                rows.append(f"- Source URL (stored): {identifier(citation['source_url'])}")
            snippet = citation.get("snippet")
            rows += ["", source_block(snippet) if isinstance(snippet, str) else "No source excerpt was stored.", ""]
        if not evidence:
            rows += ["No source excerpts were stored.", ""]
    return "\n".join(rows).rstrip() + "\n"

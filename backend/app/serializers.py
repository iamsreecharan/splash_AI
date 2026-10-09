from app.models import Alert, AnalysisRun, Repository


def iso(value):
    if not value:
        return None
    # SQLite unit tests omit timezone metadata; all persisted timestamps are UTC.
    from datetime import timezone
    return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()


def repository_json(repo: Repository):
    return {"id": repo.id, "full_name": repo.full_name, "description": repo.description,
            "default_branch": repo.default_branch, "language": repo.language,
            "status": repo.status, "indexed_files": repo.indexed_files,
            "indexed_chunks": repo.indexed_chunks, "last_indexed_at": iso(repo.last_indexed_at),
            "html_url": repo.html_url, "is_demo": repo.is_demo}


def alert_json(alert: Alert, repository: Repository):
    fields = ["id", "repository_id", "title", "summary", "severity", "confidence",
              "evidence_status", "status", "pr_number", "pr_title", "pr_url",
              "affected_component", "evidence", "recommendations", "change_type", "feedback", "ai_explanation"]
    return {**{field: getattr(alert, field) for field in fields},
            "repository_name": repository.full_name, "created_at": iso(alert.created_at)}


def run_json(run: AnalysisRun, repository: Repository):
    fields = ["id", "repository_id", "pr_number", "title", "status", "alert_count", "error", "stages", "metrics"]
    return {**{field: getattr(run, field) for field in fields},
            "repository_name": repository.full_name,
            "created_at": iso(run.created_at), "completed_at": iso(run.completed_at)}

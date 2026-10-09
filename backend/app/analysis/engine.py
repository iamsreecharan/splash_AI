"""Public entry point for deterministic, bounded analysis of two snapshots."""
from itertools import islice
from app.agents.workflow import analysis_graph
from app.ingestion.paths import source_files


def analyze_change(*, repository_name, base_files, head_files, pr_number=1, title="", base_sha="", head_sha="", ai_provider=None, retrieval_context=None, enable_ai=None):
    safe_base = source_files(base_files, strict=True)
    safe_head = source_files(head_files, strict=True)
    if enable_ai is False:
        ai_provider = None
    elif ai_provider is None:
        import os
        if os.getenv("SPLASH_AI_ENABLED", "false").lower() == "true":
            from app.retrieval.providers import OpenAIProvider
            ai_provider = OpenAIProvider.from_environment()
    state = analysis_graph().invoke({
        "repository_name": repository_name,
        "base_files": safe_base, "head_files": safe_head,
        "excluded_head_paths": sorted(set(head_files) - set(safe_head)),
        "ai_provider": ai_provider,
        "retrieval_context": list(islice(retrieval_context or (), 8)),
        "base_sha": base_sha, "head_sha": head_sha,
        "pr_number": pr_number, "title": title,
        "changes": [], "candidates": [], "alerts": [], "stages": [], "metrics": {},
    })
    return {key: state[key] for key in ("changes", "alerts", "stages", "metrics")}

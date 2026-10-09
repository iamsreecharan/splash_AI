"""Four controlled LangGraph nodes; only verified static evidence creates alerts.

No agent executes repository code, has tools, or autonomously modifies a repo.
Optional hosted providers may explain supplied evidence separately, but they
cannot grant verification or raise severity in this deterministic workflow.
"""
from functools import lru_cache
import ast
from collections import Counter
from hashlib import sha256
from time import perf_counter
from typing import TypedDict
from urllib.parse import quote

from langgraph.graph import END, START, StateGraph

from app.analysis.contracts import extract_consumers, extract_producers
from app.ingestion.indexer import index_files
from app.retrieval.hybrid import hybrid_search


class AnalysisState(TypedDict, total=False):
    repository_name: str
    base_files: dict
    head_files: dict
    base_sha: str
    head_sha: str
    pr_number: int
    title: str
    changes: list
    candidates: list
    alerts: list
    stages: list
    metrics: dict
    excluded_head_paths: list
    ai_provider: object
    retrieval_context: list
    advisory_context: dict


def _timed(name, operation):
    def run(state):
        started = perf_counter()
        result, summary = operation(state)
        result["stages"] = state["stages"] + [{"name": name, "status": "completed", "summary": summary,
                                               "duration_ms": round((perf_counter() - started) * 1000, 2)}]
        return result
    return run


def _detect(state):
    base = extract_producers(state["base_files"])
    head = extract_producers(state["head_files"])
    after = {(p["endpoint"], p["method"]): p for p in head}
    counts_before = Counter((p["endpoint"], p["method"]) for p in base)
    counts_after = Counter((p["endpoint"], p["method"]) for p in head)
    changes = []
    unknown = 0
    for before in base:
        key = (before["endpoint"], before["method"])
        if counts_before[key] > 1 or counts_after[key] > 1:
            unknown += 1
            continue
        producer = after.get((before["endpoint"], before["method"]))
        if producer is None:
            if before["path"] in state.get("excluded_head_paths", []):
                unknown += 1
                continue
            content = state["head_files"].get(before["path"])
            if content is not None:
                try:
                    tree = ast.parse(content)
                except SyntaxError:
                    unknown += 1
                    continue
                surviving = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == before["symbol"]), None)
                if surviving and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in {"get", "post", "put", "patch", "delete", "head", "options"} for d in surviving.decorator_list) and not any(p["path"] == before["path"] and p["symbol"] == before["symbol"] for p in head):
                    unknown += 1
                    continue
            replacement = next((p for p in head if p["path"] == before["path"] and p["symbol"] == before["symbol"]), None)
            changes.append({"type": "endpoint_renamed" if replacement else "endpoint_removed",
                            "endpoint": before["endpoint"], "method": before["method"],
                            "replacement_endpoint": replacement["endpoint"] if replacement else None,
                            "before": before, "after": replacement, "removed_fields": []})
        elif before["known_shape"] and producer["known_shape"]:
            removed = sorted(set(before["guaranteed_fields"]) - set(producer["fields"]))
            if removed:
                added = sorted(set(producer["fields"]) - set(before["fields"]))
                changes.append({"type": "response_field_removed", "endpoint": before["endpoint"],
                                "method": before["method"], "removed_fields": removed,
                                "possible_replacement_fields": added, "before": before, "after": producer})
        elif state["base_files"].get(before["path"]) != state["head_files"].get(before["path"]):
            unknown += 1
    metrics = {"producer_contracts": len(head), "breaking_contract_changes": len(changes),
               "unsupported_contract_shapes": unknown, "uncertain_candidates": 0,
               "verified_dependencies": 0, "model_calls": 0, "source_execution": False}
    return {"changes": changes, "metrics": metrics}, f"Found {len(changes)} breaking HTTP contract changes; {unknown} changed shapes could not be established."


def _impact(state):
    consumers = extract_consumers(state["head_files"])
    by_route = {}
    for consumer in consumers:
        by_route.setdefault((consumer["endpoint"], consumer["method"]), []).append(consumer)
    candidates = []
    for change in state["changes"]:
        for consumer in by_route.get((change["endpoint"], change["method"]), []):
            if change["type"] == "response_field_removed":
                removed = change["removed_fields"]
                fields = sorted(key for key in removed if any((field == key or field.startswith(key + ".")) and not location.get("guarded", False)
                                                             for field, location in consumer["fields"].items()))
                if not fields:
                    continue
            else:
                fields = []
            candidates.append({"change": change, "consumer": consumer, "affected_fields": fields})
    # Lexical matches can suggest investigation, but never establish a dependency.
    indexed = index_files(state["head_files"], state["repository_name"], state["head_sha"])
    advisory_context = {}
    query_embedding = None
    retrieval_metrics = {"query_embedding_calls": 0, "retrieved_context_chunks": 0,
                         "vector_context_chunks": 0}
    if state.get("ai_provider") is not None and state.get("retrieval_context") and candidates:
        # One shared query vector per run reuses the worker's bounded head
        # embedding sample, rather than embedding again for every alert.
        embedding_query = "\n".join(" ".join([change["endpoint"], change["before"]["symbol"]] + change["removed_fields"])
                                    for change in state["changes"][:20])[:1000]
        try:
            query_embedding = state["ai_provider"].embed_query(embedding_query)
            retrieval_metrics["query_embedding_calls"] = 1
        except Exception as exc:
            retrieval_metrics["retrieval_embedding_error"] = type(exc).__name__
    uncertain = 0
    for change in state["changes"][:20]:
        query = " ".join([change["endpoint"], change["before"]["symbol"]] + change["removed_fields"] + change.get("possible_replacement_fields", []))
        matches = hybrid_search(query, indexed["chunks"], query_embedding=query_embedding,
                                embedding_records=state.get("retrieval_context", []), limit=4)
        exact_paths = {c["consumer"]["path"] for c in candidates if c["change"] == change}
        hints = [match for match in matches if match["path"] not in exact_paths and match["path"] != change["before"]["path"]]
        advisory_context[f"{change['method']} {change['endpoint']}"] = hints
        uncertain += len(hints)
        retrieval_metrics["retrieved_context_chunks"] += len(hints)
        retrieval_metrics["vector_context_chunks"] += sum(match.get("retrieval", {}).get("vector_rank") is not None for match in hints)
    metrics = {**state["metrics"], "consumer_requests": len(consumers), "uncertain_candidates": uncertain,
               "indexed_chunks": len(indexed["chunks"]), "lexical_queries": min(20, len(state["changes"])),
               "lexical_query_limit_reached": len(state["changes"]) > 20, **retrieval_metrics}
    return {"candidates": candidates, "metrics": metrics, "advisory_context": advisory_context}, f"Matched {len(candidates)} consumers by exact HTTP method, endpoint and response bindings; retrieved {uncertain} unverified context hints."


def _evidence(state, path, first, last, snapshot, role):
    files = state[f"{snapshot}_files"]
    content = files.get(path, "")
    lines = content.splitlines()
    first = max(1, min(first, len(lines) or 1))
    last = max(first, min(last, first + 19, len(lines) or 1))
    commit_sha = state[f"{snapshot}_sha"]
    # Never construct a GitHub citation to a fabricated or branch-level revision.
    valid_sha = not state["repository_name"].startswith("splash-demo/") and len(commit_sha) in {40, 64} and all(c in "0123456789abcdef" for c in commit_sha.lower())
    source_url = f"https://github.com/{quote(state['repository_name'], safe='/')}/blob/{commit_sha}/{quote(path, safe='/')}#L{first}-L{last}" if valid_sha else ""
    return {"path": path, "start_line": first, "end_line": last, "snippet": "\n".join(lines[first - 1:last]),
            "source_url": source_url, "commit_sha": commit_sha, "role": role,
            "snapshot": snapshot, "snapshot_hash": sha256(content.encode()).hexdigest()}


def _verify(state):
    alerts = []
    seen = set()
    for candidate in state["candidates"]:
        change, consumer = candidate["change"], candidate["consumer"]
        fields = candidate["affected_fields"]
        key = (change["endpoint"], change["method"], consumer["path"], tuple(fields))
        if key in seen:
            continue
        seen.add(key)
        before, after = change["before"], change["after"]
        producer_line = before["route_line"]
        producer_end = producer_line + 4
        if fields:
            # Bind producer evidence to the specific old field, not a token match.
            removed_field = next(key for key in change["removed_fields"]
                                 if any(field == key or field.startswith(key + ".") for field in fields))
            field_lines = before["fields"][removed_field]
            producer_line, producer_end = field_lines["start_line"], field_lines["end_line"]
        evidence = [_evidence(state, before["path"], producer_line, producer_end, "base", "producer_before")]
        if after:
            evidence.append(_evidence(state, after["path"], after["route_line"], min(after["end_line"], after["route_line"] + 12), "head", "producer_after"))
        evidence.append(_evidence(state, consumer["path"], consumer["fetch_line"], consumer["fetch_line"] + 2, "head", "consumer_request"))
        if fields:
            usage_lines = [location for field, location in consumer["fields"].items()
                           if any(field == key or field.startswith(key + ".") for key in fields)]
            first = min(location["start_line"] for location in usage_lines)
            last = max(location["end_line"] for location in usage_lines)
            evidence.append(_evidence(state, consumer["path"], first, last, "head", "consumer_usage"))
        location = consumer["path"].rsplit("/", 1)[-1]
        if fields:
            names = ", ".join(fields)
            title = f"{location} still reads removed response field {names}"
            summary = f"{change['method']} {change['endpoint']} no longer returns {names}. {consumer['path']} still fetches that endpoint and reads the removed field from its JSON response."
        else:
            title = f"{location} still calls a removed endpoint"
            summary = f"{change['method']} {change['endpoint']} was removed or moved, while {consumer['path']} still calls the original route."
        alerts.append({"title": title, "summary": summary, "severity": "high", "confidence": 0.97,
                       "evidence_status": "verified", "status": "open", "pr_number": state["pr_number"],
                       "pr_title": state["title"], "pr_url": f"https://github.com/{state['repository_name']}/pull/{state['pr_number']}",
                       "affected_component": consumer["path"], "change_type": change["type"],
                       "evidence": evidence, "recommendations": [], "affected_fields": fields,
                       "replacement_fields": change.get("possible_replacement_fields", []),
                       "replacement_endpoint": change.get("replacement_endpoint"),
                       "_context_key": f"{change['method']} {change['endpoint']}"})
    return {"alerts": alerts, "metrics": {**state["metrics"], "verified_dependencies": len(alerts)}}, f"Verified {len(alerts)} impacts against source bindings and exact snapshot citations."


def _resolve(state):
    alerts = []
    metrics = dict(state["metrics"])
    provider = state.get("ai_provider")
    for alert in state["alerts"]:
        updated = dict(alert)
        context_key = updated.pop("_context_key", "")
        if alert["affected_fields"]:
            recommendations = [f"Update {alert['affected_component']} to the intended response schema and add a producer–consumer contract test.",
                               "If existing clients must keep working, retain the previous response key during a migration."]
            if alert["replacement_fields"]:
                recommendations.append(f"New response fields include {', '.join(alert['replacement_fields'])}; confirm semantics before using one as a replacement.")
        else:
            recommendations = [f"Update the request in {alert['affected_component']} and its HTTP contract test.",
                               "Keep the previous route as a compatibility alias if other clients still depend on it."]
            if alert["replacement_endpoint"]:
                recommendations.append(f"The same producer function now exposes {alert['replacement_endpoint']}; confirm this is the intended replacement.")
        updated["recommendations"] = recommendations
        if provider is not None and len(alerts) < 3:
            try:
                # Static evidence and retrieved hints carry separate authority.
                # Hints never enter the verified evidence list or affect severity.
                context = [{**item, "verified": True, "evidence_status": "verified"} for item in alert["evidence"]]
                for hint in state.get("advisory_context", {}).get(context_key, [])[:3]:
                    context.append({**hint, "snippet": hint["content"][:600], "role": "retrieval_context",
                                    "verified": False, "evidence_status": "needs_investigation"})
                explanation = provider.reason(alert["summary"], context)
                updated["ai_explanation"] = explanation
                metrics["model_calls"] += 1
            except Exception as exc:
                # Provider failures never erase deterministic findings. Record
                # only the exception type; API exception text may expose secrets.
                metrics["ai_error"] = type(exc).__name__
        alerts.append(updated)
    return {"alerts": alerts, "metrics": metrics}, f"Prepared source-grounded recommendations for {len(alerts)} verified alerts; {metrics['model_calls']} optional hosted explanations."


@lru_cache(maxsize=1)
def analysis_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("change_detection", _timed("Change Detection", _detect))
    graph.add_node("impact_analysis", _timed("Impact Analysis", _impact))
    graph.add_node("verification", _timed("Verification", _verify))
    graph.add_node("resolution", _timed("Resolution", _resolve))
    graph.add_edge(START, "change_detection")
    graph.add_edge("change_detection", "impact_analysis")
    graph.add_edge("impact_analysis", "verification")
    graph.add_edge("verification", "resolution")
    graph.add_edge("resolution", END)
    return graph.compile()

import pytest

from app.analysis.contracts import extract_consumers, extract_producers
from app.analysis.engine import analyze_change
from app.analysis.fixtures import BASE_FILES, RENAME_FILES, SAFE_FILES
from app.ingestion.paths import IncompleteSnapshotError, MAX_FILE_BYTES
from app.ingestion.indexer import index_files
from evals.cases import CASES, consumer, producer
from evals.run import evaluate


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_ground_truth_contract_cases(case):
    result = analyze_change(repository_name="eval/fixture", base_files=case.base, head_files=case.head,
                            base_sha="a" * 40, head_sha="b" * 40)
    assert len(result["alerts"]) == case.expected_alerts
    for alert in result["alerts"]:
        assert alert["evidence_status"] == "verified"
        assert set(alert["affected_fields"]) == set(case.expected_fields)
        assert alert["affected_component"] == case.consumer_path


def test_machine_demo_and_harmless_addition():
    rename = analyze_change(repository_name="demo/machine", base_files=BASE_FILES, head_files=RENAME_FILES)
    assert len(rename["alerts"]) == 1
    assert rename["alerts"][0]["affected_fields"] == ["temperature"]
    assert [stage["name"] for stage in rename["stages"]] == ["Change Detection", "Impact Analysis", "Verification", "Resolution"]
    assert rename["metrics"]["model_calls"] == 0
    assert analyze_change(repository_name="demo/machine", base_files=BASE_FILES, head_files=SAFE_FILES)["alerts"] == []


def test_evaluation_reports_separate_detection_and_citation_metrics():
    report = evaluate()
    assert report["cases"] >= 60
    assert report["precision"] == report["recall"] == 1
    assert report["evidence_faithfulness"] == 1
    assert report["evidence_items_checked"] > report["true_positive"]


def test_oversized_snapshot_cannot_be_treated_as_endpoint_removal():
    with pytest.raises(IncompleteSnapshotError):
        analyze_change(repository_name="eval/fixture", base_files=BASE_FILES,
                       head_files={**BASE_FILES, "api/machines.py": "#" * (MAX_FILE_BYTES + 1)})


def test_secret_filtered_head_cannot_be_treated_as_endpoint_removal():
    head = {**BASE_FILES, "api/machines.py": BASE_FILES["api/machines.py"] + '\napi_key = "sk-' + "x" * 30 + '"\n'}
    result = analyze_change(repository_name="eval/fixture", base_files=BASE_FILES, head_files=head)
    assert result["alerts"] == []
    assert result["metrics"]["unsupported_contract_shapes"] == 1


def test_exact_citations_reference_real_lines_and_given_revisions():
    result = analyze_change(repository_name="eval/fixture", base_files=BASE_FILES, head_files=RENAME_FILES,
                            base_sha="a" * 40, head_sha="b" * 40)
    for evidence in result["alerts"][0]["evidence"]:
        files = BASE_FILES if evidence["snapshot"] == "base" else RENAME_FILES
        text = files[evidence["path"]].splitlines()
        assert evidence["snippet"] == "\n".join(text[evidence["start_line"] - 1:evidence["end_line"]])
        assert evidence["commit_sha"] in evidence["source_url"]


def test_nested_response_and_path_parameters():
    base = {"api/service.py": producer().replace('"/api/items"', '"/api/items/{item_id}"').replace('"total": 12', '"order": {"total": 12}'),
            "web/consumer.ts": consumer().replace('"/api/items"', '`/api/items/${itemId}`').replace('data.total', 'data.order.total')}
    head = {**base, "api/service.py": base["api/service.py"].replace('"total": 12', '"new_total": 12')}
    result = analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)
    assert result["alerts"][0]["affected_fields"] == ["order.total"]


def test_response_model_unknown_does_not_invent_removed_fields():
    base = {"api/service.py": producer(), "web/consumer.ts": consumer()}
    head = {**base, "api/service.py": producer().replace('{"total": 12, "id": "record-1"}', 'payload.model_dump()')}
    result = analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)
    assert not result["alerts"]
    assert result["metrics"]["unsupported_contract_shapes"] == 1


def test_http_methods_disambiguate_dependencies():
    base = {"api/service.py": producer(), "web/consumer.ts": consumer().replace('fetch("/api/items")', 'fetch("/api/items", {method: "POST"})')}
    head = {**base, "api/service.py": producer("new_total")}
    assert not analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)["alerts"]


def test_router_prefix_and_import_mount():
    files = {"api/routes.py": producer().replace('from fastapi import FastAPI', 'from fastapi import APIRouter').replace('app = FastAPI()', 'router = APIRouter(prefix="/v1")').replace('@app.', '@router.'),
             "api/main.py": 'from fastapi import FastAPI\nfrom api.routes import router\napp = FastAPI()\napp.include_router(router, prefix="/internal")\n'}
    assert extract_producers(files)[0]["endpoint"] == "/internal/v1/api/items"


def test_local_alias_mutation_does_not_verify_server_field_dependency():
    client = consumer().replace('return data.total;', 'const local = data; local.total = 99; return data.total;')
    base = {"api/service.py": producer(), "web/consumer.ts": client}
    head = {**base, "api/service.py": producer("new_total")}
    assert not analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)["alerts"]


def test_hosted_explanations_never_replace_verified_rules():
    class Provider:
        def reason(self, query, chunks):
            return {"summary": "Hypothesis", "verified": False, "evidence_status": "needs_investigation"}
    result = analyze_change(repository_name="eval/fixture", base_files=BASE_FILES, head_files=RENAME_FILES, ai_provider=Provider())
    assert result["metrics"]["model_calls"] == 1
    assert result["alerts"][0]["evidence_status"] == "verified"
    assert result["alerts"][0]["ai_explanation"]["verified"] is False


def test_hosted_failure_preserves_deterministic_results_without_secrets():
    class Provider:
        def reason(self, query, chunks):
            raise RuntimeError("sensitive provider response")
    result = analyze_change(repository_name="eval/fixture", base_files=BASE_FILES, head_files=RENAME_FILES, ai_provider=Provider())
    assert len(result["alerts"]) == 1
    assert result["metrics"]["ai_error"] == "RuntimeError"
    assert "sensitive provider response" not in str(result)


def test_mutated_python_payload_shape_is_unknown():
    base = {"api/service.py": producer(), "web/consumer.ts": consumer()}
    head = {**base, "api/service.py": producer().replace('return {"total": 12, "id": "record-1"}', 'payload = {"id": "record-1"}\n    payload["total"] = 12\n    return payload')}
    assert not analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)["alerts"]


def test_response_model_filter_and_duplicate_routes_are_unalerted():
    client = consumer()
    base = {"api/service.py": producer().replace('@app.get("/api/items")', '@app.get("/api/items", response_model=Schema)'), "web/consumer.ts": client}
    head = {**base, "api/service.py": base["api/service.py"].replace('"total": 12', '"new_total": 12')}
    assert not analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)["alerts"]
    base = {"api/service.py": producer(), "api/other.py": producer("other"), "web/consumer.ts": client}
    head = {**base, "api/service.py": producer("new_total")}
    assert not analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)["alerts"]


@pytest.mark.parametrize("client", [
    consumer().replace("await response.json()", "response.json()"),
    consumer().replace('await fetch("/api/items")', 'fetch("/api/items")'),
    consumer().replace('const data = await response.json();', 'response.json = async () => ({total: 7}); const data = await response.json();'),
])
def test_unawaited_or_rebound_json_is_not_verified(client):
    base = {"api/service.py": producer(), "web/consumer.ts": client}
    head = {**base, "api/service.py": producer("new_total")}
    assert not analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)["alerts"]


def test_custom_fastapi_named_constructor_does_not_prove_http_contract():
    files = {"api/service.py": producer().replace('from fastapi import FastAPI', 'class FastAPI:\n    pass')}
    assert not extract_producers(files)


@pytest.mark.parametrize("body", [
    "return data.total ?? 0;", "return data.total || 0;", "return data.total?.toFixed();",
    "delete data.total; return data.total;", "enrich(data); return data.total;",
])
def test_guarded_or_mutated_response_reads_do_not_raise_high_alerts(body):
    client = consumer().replace("return data.total;", body)
    base = {"api/service.py": producer(), "web/consumer.ts": client}
    head = {**base, "api/service.py": producer("new_total")}
    assert not analyze_change(repository_name="eval/fixture", base_files=base, head_files=head)["alerts"]


def test_retrieved_markdown_context_reaches_provider_without_granting_verification():
    class Provider:
        contexts = []
        def reason(self, query, chunks):
            self.contexts.append(chunks)
            return {"summary": "Review the machine response contract", "verified": False}
    provider = Provider()
    base = {**BASE_FILES, "docs/thermal-contract.md": '# Machine readings\nThe temperature response field drives dashboard thermal display.\n',
            "docs/unrelated.md": '# Developer tools\nInstall the formatter before working on styling.\n'}
    head = {**base, **RENAME_FILES}
    result = analyze_change(repository_name="eval/fixture", base_files=base, head_files=head,
                            base_sha="a" * 40, head_sha="b" * 40, ai_provider=provider)
    hints = [item for item in provider.contexts[0] if item["role"] == "retrieval_context"]
    assert any(item["path"] == "docs/thermal-contract.md" for item in hints)
    assert all(item["path"] != "docs/unrelated.md" for item in hints)
    assert all(item["commit_sha"] == "b" * 40 and item["verified"] is False for item in hints)
    assert result["alerts"][0]["evidence_status"] == "verified"
    assert all(item["path"] != "docs/thermal-contract.md" for item in result["alerts"][0]["evidence"])


def test_bounded_query_embedding_adds_semantic_context_and_rejects_stale_vectors():
    class Provider:
        calls = 0
        contexts = []
        def embed_query(self, query):
            self.calls += 1
            return [1.0, 0.0, 0.0]
        def reason(self, query, chunks):
            self.contexts.append(chunks)
            return {"summary": "Unverified context", "verified": False}
    provider = Provider()
    base = {**BASE_FILES, "docs/thermal-design.md": '# Thermal display\nShow degrees Celsius for each operating unit.\n',
            "docs/formatter.md": '# Formatter\nUse consistent indentation.\n'}
    head = {**base, **RENAME_FILES}
    chunks = index_files(head, "eval/fixture", "b" * 40)["chunks"]
    docs = [chunk for chunk in chunks if chunk["kind"] == "documentation"]
    records = [{**chunk, "snippet": chunk["content"], "embedding": [1.0, 0.0, 0.0] if "thermal-design" in chunk["path"] else [0.0, 1.0, 0.0]} for chunk in docs]
    # A stale record cannot earn context even with a perfect vector match.
    records.append({**docs[0], "commit_sha": "c" * 40, "embedding": [1.0, 0.0, 0.0]})
    result = analyze_change(repository_name="eval/fixture", base_files=base, head_files=head,
                            base_sha="a" * 40, head_sha="b" * 40, ai_provider=provider, retrieval_context=records)
    hints = [item for item in provider.contexts[0] if item["role"] == "retrieval_context"]
    assert provider.calls == 1
    assert any(item["path"] == "docs/thermal-design.md" for item in hints)
    assert all(item["path"] != "docs/formatter.md" and item["commit_sha"] == "b" * 40 for item in hints)
    assert result["metrics"]["vector_context_chunks"] >= 1
    assert len(result["alerts"]) == 1

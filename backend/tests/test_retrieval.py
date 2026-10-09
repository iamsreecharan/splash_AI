"""Retrieval safeguards: grounding, bounded hosted requests, and tenant scope."""

from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.retrieval.lexical import LexicalRetriever, search_chunks, tokenize
from app.retrieval.hybrid import hybrid_search
from app.retrieval.providers import (
    OpenAIProvider, PgVectorStore, ProviderBudgetError, ProviderConfig,
    ProviderDisabledError, ProviderInputError, ProviderResponseError,
)


def chunk(path="pricing.py", content="def calculate_total(price): return price", **kwargs):
    return {"path": path, "content": content, "start_line": 4, "end_line": 6,
            "symbol": "calculate_total", "kind": "function", "commit_sha": "abc123", **kwargs}


class FakeClient:
    def __init__(self, payload=None, dimensions=3):
        self.calls = []
        self.payload = payload or {"summary": "Potential impact", "hypotheses": [], "recommendations": []}
        self.dimensions = dimensions
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.reason))
        self.embeddings = SimpleNamespace(create=self.embed)

    def reason(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))])

    def embed(self, **kwargs):
        self.calls.append(kwargs)
        # Return in reverse order to check that responses are matched by index.
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[float(i + 1)] * self.dimensions)
                                     for i in reversed(range(len(kwargs["input"])))])


class LexicalRetrievalTests(unittest.TestCase):
    def test_identifier_components_and_exact_symbols_are_searchable(self):
        self.assertIn("calculate_total", tokenize("calculate_total"))
        self.assertIn("total", tokenize("calculateTotal"))
        results = search_chunks("calculate_total", [chunk(), chunk("readme.py", "total = 0", symbol="other")])
        self.assertEqual(results[0]["path"], "pricing.py")

    def test_citation_preserves_revision_and_lines_without_mutating_input(self):
        source = chunk()
        result = search_chunks("price", [source], max_snippet_chars=12)[0]
        self.assertEqual(result["citation"]["sha"], "abc123")
        self.assertEqual(result["citation"]["start_line"], 4)
        self.assertEqual(result["citation"]["end_line"], 6)
        self.assertEqual(result["snippet"], source["content"][:12])
        self.assertEqual(result["content"], source["content"])
        self.assertNotIn("score", source)
        self.assertNotIn("verified", result)

    def test_no_revision_is_fabricated_for_unbound_chunks(self):
        source = chunk()
        del source["commit_sha"]
        self.assertEqual(search_chunks("price", [source])[0]["citation"]["sha"], "")
        source["sha"] = "head-revision"
        self.assertEqual(search_chunks("price", [source])[0]["citation"]["sha"], "head-revision")

    def test_order_is_deterministic_when_input_order_changes(self):
        sources = [chunk("z.py"), chunk("a.py")]
        self.assertEqual(search_chunks("price", sources), search_chunks("price", reversed(sources)))
        self.assertEqual(search_chunks("price", sources)[0]["path"], "a.py")

    def test_empty_or_unmatched_queries_produce_no_evidence(self):
        self.assertEqual(search_chunks("unrelated_identifier", [chunk()]), [])
        self.assertEqual(search_chunks("the and", [chunk()]), [])
        self.assertEqual(search_chunks("price", [], limit=0), [])

    def test_document_adapter_preserves_citation_metadata(self):
        document = LexicalRetriever([chunk()]).documents("price")[0]
        self.assertIn("price", document.page_content)
        self.assertEqual(document.metadata["commit_sha"], "abc123")


class HostedProviderTests(unittest.TestCase):
    def config(self, **kwargs):
        return ProviderConfig(enabled=True, embedding_dimensions=3, **kwargs)

    def test_provider_never_calls_network_by_default(self):
        client = FakeClient()
        provider = OpenAIProvider(client=client)
        with self.assertRaises(ProviderDisabledError):
            provider.reason("impact?", [chunk()])
        with self.assertRaises(ProviderDisabledError):
            provider.embed_chunks([chunk()])
        self.assertEqual(client.calls, [])

    def test_cost_and_token_limits_reject_before_request(self):
        for config in [self.config(max_input_tokens=20), self.config(max_request_usd=0.0000001),
                       self.config(max_total_usd=0.0000001)]:
            client = FakeClient()
            with self.assertRaises(ProviderBudgetError):
                OpenAIProvider(config, client=client).reason("impact?", [chunk()])
            self.assertEqual(client.calls, [])

    def test_context_has_bounded_snippets_and_supplied_citations_only(self):
        client = FakeClient({
            "summary": "Investigate consumers", "verified": True, "evidence_status": "verified",
            "hypotheses": [{"claim": "A hypothesis", "citation_ids": ["C1"], "verified": True},
                           {"claim": "Invented source", "citation_ids": ["C1", "C99"]},
                           {"claim": "No source", "citation_ids": []}],
            "recommendations": ["Review the caller"],
        })
        provider = OpenAIProvider(self.config(max_snippets=1, max_snippet_chars=16), client=client)
        result = provider.reason("impact?", [chunk(), chunk("never-sent.py")])
        context = json.loads(client.calls[0]["messages"][1]["content"])["evidence"]
        self.assertEqual(len(context), 1)
        self.assertEqual(len(context[0]["snippet"]), 16)
        self.assertEqual(result["citations"]["C1"]["sha"], "abc123")
        self.assertEqual(len(result["hypotheses"]), 1)
        self.assertFalse(result["verified"])
        self.assertFalse(result["hypotheses"][0]["verified"])
        self.assertEqual(result["evidence_status"], "needs_investigation")
        self.assertNotIn("tools", client.calls[0])
        self.assertGreater(provider.reserved_usd, 0)

    def test_embeddings_are_aligned_by_index_and_reject_oversize_batch(self):
        client = FakeClient()
        provider = OpenAIProvider(self.config(max_snippets=2), client=client)
        result = provider.embed_chunks([chunk("a.py"), chunk("b.py")])
        self.assertEqual([row["citation"]["path"] for row in result], ["a.py", "b.py"])
        self.assertEqual(result[0]["embedding"], [1.0, 1.0, 1.0])
        with self.assertRaises(ProviderBudgetError):
            provider.embed_chunks([chunk(), chunk(), chunk()])
        self.assertEqual(len(client.calls), 1)

    def test_bad_dimensions_or_nonfinite_embeddings_are_rejected(self):
        client = FakeClient(dimensions=2)
        with self.assertRaises(ValueError):
            OpenAIProvider(self.config(), client=client).embed_chunks([chunk()])

    def test_failed_requests_still_consume_a_budget_reservation(self):
        client = FakeClient()
        def fail(**kwargs):
            raise RuntimeError("transport failed")
        client.chat.completions.create = fail
        provider = OpenAIProvider(self.config(), client=client)
        with self.assertRaises(RuntimeError):
            provider.reason("impact?", [chunk()])
        self.assertGreater(provider.reserved_usd, 0)

    def test_configuration_does_not_expose_api_key_and_rejects_unknown_prices(self):
        self.assertNotIn("example-secret", repr(ProviderConfig(api_key="example-secret")))
        with self.assertRaises(ValueError):
            replace(ProviderConfig(), reasoning_model="unpriced-model")
        with self.assertRaises(ValueError):
            replace(ProviderConfig(), max_snippets=1.5)

    def test_environment_opt_in_uses_only_the_splash_owned_api_key(self):
        with patch.dict("os.environ", {"SPLASH_AI_ENABLED": "true", "SPLASH_OPENAI_API_KEY": "splash-test-key",
                                       "OPENAI_API_KEY": "platform-owned-key"}, clear=True):
            provider = OpenAIProvider.from_environment(client=FakeClient())
            self.assertTrue(provider.config.enabled)
            self.assertEqual(provider.config.api_key, "splash-test-key")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "platform-owned-key"}, clear=True):
            self.assertIsNone(OpenAIProvider.from_environment().config.api_key)
            self.assertFalse(OpenAIProvider.from_environment().config.enabled)

    def test_settings_adapter_preserves_budgets_and_explicit_enabled_flag(self):
        settings = SimpleNamespace(ai_enabled=True, openai_api_key="splash-test-key", openai_model="gpt-4o-mini",
                                   ai_max_request_usd=0.005)
        provider = OpenAIProvider.from_settings(settings, client=FakeClient())
        self.assertEqual(provider.config.max_request_usd, 0.005)
        self.assertEqual(provider.config.reasoning_model, "gpt-4o-mini")

    def test_default_provider_total_budget_matches_the_low_cost_job_limit(self):
        self.assertEqual(ProviderConfig().max_total_usd, 0.02)
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(OpenAIProvider.from_environment().config.max_total_usd, 0.02)

    def test_unsafe_paths_and_inline_credentials_never_reach_either_api(self):
        token = "ghp_" + "x" * 30
        for source in [chunk(".env"), chunk("config/credentials.py"), chunk("../outside.py"),
                       chunk(content="prefix " + token),
                       chunk(content="safe prefix " * 100 + token, snippet="safe clipped snippet"),
                       chunk(content="safe source", snippet=token), chunk(path=f"{token}.py")]:
            client = FakeClient()
            provider = OpenAIProvider(self.config(max_snippet_chars=10), client=client)
            with self.assertRaises(ProviderInputError) as caught:
                provider.embed_chunks([source])
            self.assertNotIn(token, str(caught.exception))
            with self.assertRaises(ProviderInputError) as caught:
                provider.reason("impact?", [source])
            self.assertNotIn(token, str(caught.exception))
            self.assertEqual(client.calls, [])
            self.assertEqual(provider.reserved_usd, 0)

    def test_possible_credential_in_reasoning_query_is_rejected_without_exposure(self):
        token = "sk-proj-" + "x" * 30
        client = FakeClient()
        provider = OpenAIProvider(self.config(), client=client)
        with self.assertRaises(ProviderInputError) as caught:
            provider.reason(f"Review this {token}", [chunk()])
        self.assertNotIn(token, str(caught.exception))
        self.assertEqual(client.calls, [])

    def test_query_embeddings_share_budget_and_reject_secrets_and_wrong_dimensions(self):
        client = FakeClient()
        provider = OpenAIProvider(self.config(max_total_usd=0.00001), client=client)
        self.assertEqual(provider.embed_query("units"), [1.0, 1.0, 1.0])
        self.assertEqual(client.calls[0]["input"], ["units"])
        with self.assertRaises(ProviderBudgetError):
            provider.embed_query("units")
        self.assertEqual(len(client.calls), 1)
        secret_client = FakeClient()
        with self.assertRaises(ProviderInputError):
            OpenAIProvider(self.config(), client=secret_client).embed_query("ghp_" + "x" * 30)
        self.assertEqual(secret_client.calls, [])
        with self.assertRaises(ValueError):
            OpenAIProvider(self.config(), client=FakeClient(dimensions=2)).embed_query("units")

    def test_source_labels_survive_in_model_context_without_verifying_model_output(self):
        client = FakeClient()
        provider = OpenAIProvider(self.config(), client=client)
        result = provider.reason("Review unit semantics", [
            chunk(role="producer_before", evidence_status="verified", verified=True),
            chunk("docs/money.md", "Values are denominated in cents", role="retrieval_context",
                  evidence_status="needs_investigation", verified=False),
        ])
        context = json.loads(client.calls[0]["messages"][1]["content"])["evidence"]
        self.assertEqual(context[0]["role"], "producer_before")
        self.assertTrue(context[0]["verified"])
        self.assertEqual(context[1]["role"], "retrieval_context")
        self.assertFalse(context[1]["verified"])
        self.assertEqual(context[1]["evidence_status"], "needs_investigation")
        self.assertFalse(result["verified"])

    def test_invalid_model_json_shape_is_rejected(self):
        client = FakeClient()
        client.payload = ["not an object"]
        with self.assertRaises(ProviderResponseError):
            OpenAIProvider(self.config(), client=client).reason("impact?", [chunk()])


class RecordingVectorStore(PgVectorStore):
    def __init__(self):
        super().__init__(None, dimensions=3)
        self.calls = []

    def _execute(self, sql, parameters=None):
        self.calls.append((sql, dict(parameters or {})))
        return SimpleNamespace(rowcount=1, mappings=lambda: [{
            "path": "pricing.py", "start_line": 4, "end_line": 6, "content": "price",
            "commit_sha": "abc123", "score": 0.9, "symbol": "price", "kind": "function",
        }])


class VectorScopingTests(unittest.TestCase):
    def test_untrusted_repository_and_source_values_use_bound_parameters(self):
        store = RecordingVectorStore()
        malicious = "repo'; DROP TABLE splash_chunk_embeddings;--"
        store.upsert([{**chunk(), "embedding": [1, 2, 3]}], organization_id="tenant-1",
                     repository_id=malicious, commit_sha="abc123")
        sql, params = store.calls[0]
        self.assertNotIn(malicious, sql)
        self.assertEqual(params["repository_id"], malicious)
        self.assertEqual(params["organization_id"], "tenant-1")
        result = store.search([1, 2, 3], organization_id="tenant-1", repository_id=malicious, commit_sha="abc123")
        sql, params = store.calls[-1]
        self.assertIn("r.organization_id = :organization_id", sql)
        self.assertIn("s.commit_sha = :commit_sha", sql)
        self.assertEqual(result[0]["citation"]["sha"], "abc123")
        store.delete_repository(organization_id="tenant-1", repository_id=malicious)
        self.assertIn("organization_id = :organization_id", store.calls[-1][0])

    def test_missing_scope_or_mismatched_snapshot_fails_without_sql(self):
        store = RecordingVectorStore()
        with self.assertRaises(ValueError):
            store.search([1, 2, 3], organization_id="", repository_id="repo", commit_sha="abc123")
        with self.assertRaises(ValueError):
            store.search([1, 2, 3], organization_id="tenant", repository_id="repo", commit_sha=None)
        with self.assertRaises(ValueError):
            store.upsert([{**chunk(), "embedding": [1, 2, 3]}], organization_id="tenant",
                         repository_id="repo", commit_sha="wrong-sha")
        with self.assertRaises(ValueError):
            store.search([float("nan"), 1, 2], organization_id="tenant", repository_id="repo", commit_sha="abc123")
        self.assertEqual(store.calls, [])


class HybridRetrievalTests(unittest.TestCase):
    def source(self, **kwargs):
        return chunk("docs/money.md", "Consumers expect monetary totals denominated in cents.",
                     symbol="", kind="documentation", **kwargs)

    def record(self, source, embedding=(1.0, 0.0), **kwargs):
        return {"citation": {key: source[key] for key in ("path", "start_line", "end_line", "commit_sha", "symbol", "kind")},
                "snippet": source["content"][:30], "embedding": list(embedding), **kwargs}

    def test_semantic_rank_selects_source_with_no_lexical_overlap(self):
        source = self.source()
        self.assertEqual(hybrid_search("invoice conversion", [source]), [])
        result = hybrid_search("invoice conversion", [source], query_embedding=[1, 0],
                               embedding_records=[self.record(source)])[0]
        self.assertEqual(result["path"], "docs/money.md")
        self.assertIsNone(result["retrieval"]["lexical_rank"])
        self.assertEqual(result["retrieval"]["vector_rank"], 1)
        self.assertAlmostEqual(result["retrieval"]["vector_similarity"], 1.0)
        self.assertFalse(result["verified"])
        self.assertEqual(result["evidence_status"], "needs_investigation")
        self.assertEqual(result["content"], source["content"])

    def test_generic_http_terms_do_not_select_unrelated_documentation(self):
        unrelated = chunk("docs/events.md", "GET the current API response for events", symbol="", kind="documentation")
        relevant = chunk("billing.py", "def invoice_total(): return total", symbol="invoice_total")
        self.assertEqual(hybrid_search("GET current API response", [unrelated]), [])
        results = hybrid_search("GET /api/current invoice_total changed", [unrelated, relevant])
        self.assertEqual([result["path"] for result in results], ["billing.py"])

    def test_stale_or_spoofed_citation_and_content_records_are_discarded(self):
        source = self.source()
        for changed_citation in [{"commit_sha": "old-sha"}, {"path": "docs/other.md"},
                                 {"start_line": 1}, {"end_line": 9}, {"symbol": "other"},
                                 {"sha": "conflicting-sha"}]:
            record = self.record(source)
            record["citation"].update(changed_citation)
            self.assertEqual(hybrid_search("invoice conversion", [source], query_embedding=[1, 0],
                                          embedding_records=[record]), [])
        for text in ["Invented text", "", "Consumers expect altered behavior"]:
            self.assertEqual(hybrid_search("invoice conversion", [source], query_embedding=[1, 0],
                                          embedding_records=[self.record(source, snippet=text)]), [])

    def test_wrong_dimensions_nonfinite_and_distant_vectors_are_discarded(self):
        source = self.source()
        for embedding in [(1,), (float("nan"), 0), (0, 0), (-1, 0), (0, 1)]:
            self.assertEqual(hybrid_search("invoice conversion", [source], query_embedding=[1, 0],
                                          embedding_records=[self.record(source, embedding)]), [])

    def test_reciprocal_rank_fusion_rewards_both_retrieval_channels(self):
        source = self.source(verified=True)
        other = chunk("other.py", "totals totals totals totals", symbol="totals")
        results = hybrid_search("totals", [source, other], query_embedding=[1, 0],
                                embedding_records=[self.record(source)])
        self.assertEqual(results[0]["path"], source["path"])
        self.assertEqual(results[0]["retrieval"]["lexical_rank"], 2)
        self.assertEqual(results[0]["retrieval"]["vector_rank"], 1)
        self.assertAlmostEqual(results[0]["score"], 1 / 62 + 1 / 61)
        self.assertFalse(results[0]["verified"])
        self.assertTrue(source["verified"])

    def test_only_eight_records_are_inspected_and_unbound_snapshot_is_not_trusted(self):
        source = self.source()
        invalid = self.record(source, embedding=(0, 0))
        self.assertEqual(hybrid_search("invoice conversion", [source], query_embedding=[1, 0],
                                      embedding_records=[invalid] * 8 + [self.record(source)]), [])
        source["commit_sha"] = ""
        self.assertEqual(hybrid_search("invoice conversion", [source], query_embedding=[1, 0],
                                      embedding_records=[self.record(source)]), [])


if __name__ == "__main__":
    unittest.main()

"""Lexical/vector reciprocal-rank fusion over exact indexed source chunks.

Embedding records are contextual inputs, not source truth. A record can enter
ranking only after its revision, file, line range and embedded text match a
supplied source chunk. Retrieved context always needs investigation.
"""

from __future__ import annotations

from collections import Counter
from itertools import islice
import math
from typing import Any, Iterable, Mapping

from app.ingestion.paths import SECRET_PATTERN, allowed_path
from .lexical import citation_for, tokenize

_GENERIC = frozenset(
    "api current get post put delete patch head options http https request requests "
    "response responses endpoint endpoints field fields route routes removed renamed "
    "changed change new old still no longer returns fetch json com www github blob "
    "source code contract schema".split()
)
_RRF_CONSTANT = 60
_MIN_COSINE_SIMILARITY = 0.35
_MAX_VECTOR_RECORDS = 8
_MAX_DIMENSIONS = 1536


def _identity(chunk: Mapping[str, Any]) -> tuple[Any, ...]:
    citation = citation_for(chunk)
    return (citation["commit_sha"], citation["path"], citation["start_line"], citation["end_line"],
            citation["symbol"], citation["kind"])


def _vector(value: Any) -> list[float] | None:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return None
    try:
        values = [float(item) for item in islice(value, _MAX_DIMENSIONS + 1)]
    except (TypeError, ValueError, OverflowError):
        return None
    if not values or len(values) > _MAX_DIMENSIONS or any(not math.isfinite(item) for item in values):
        return None
    scale = max(abs(item) for item in values)
    if scale == 0:
        return None
    # Scaling first avoids overflow for large finite embeddings.
    scaled = [item / scale for item in values]
    norm = math.sqrt(sum(item * item for item in scaled))
    return [item / norm for item in scaled]


def _lexical_rank(query: str, chunks: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    terms = set(tokenize(query)) - _GENERIC
    if not terms:
        return []
    fields = [(Counter(tokenize(str(chunk.get("symbol") or ""))),
               Counter(tokenize(chunk["path"])), Counter(tokenize(chunk["content"]))) for chunk in chunks]
    counts = Counter(token for row in fields for token in set().union(*(set(field) for field in row)))
    scored = []
    for chunk, row in zip(chunks, fields, strict=True):
        score = 0.0
        for token in sorted(terms):
            frequency = sum(weight * field[token] for weight, field in zip((3, 2, 1), row, strict=True))
            if frequency:
                score += (1 + math.log((len(chunks) + 1) / (counts[token] + 1))) * (1 + math.log(frequency))
        if score > 0:
            scored.append((score, _identity(chunk)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [identity for _, identity in scored]


def _matched_source(record: Mapping[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    citation = record.get("citation") or record
    if not isinstance(citation, Mapping):
        return None
    record_sha = citation.get("commit_sha") or citation.get("sha")
    if not record_sha or (citation.get("sha") and citation.get("commit_sha")
                          and citation["sha"] != citation["commit_sha"]):
        return None
    embedded_texts = [record[key] for key in ("snippet", "content") if key in record]
    if not embedded_texts or any(not isinstance(text, str) or not text for text in embedded_texts):
        return None
    for source in chunks:
        source_citation = citation_for(source)
        if any(source_citation[key] != citation.get(key) for key in ("path", "start_line", "end_line")):
            continue
        if source_citation["commit_sha"] != record_sha:
            continue
        if any(key in citation and source_citation[key] != citation[key] for key in ("symbol", "kind")):
            continue
        if all(source["content"].startswith(text) for text in embedded_texts):
            return source
    return None


def hybrid_search(
    query: str,
    chunks: Iterable[Mapping[str, Any]],
    *,
    query_embedding: Iterable[float] | None = None,
    embedding_records: Iterable[Mapping[str, Any]] = (),
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Fuse lexical and validated semantic ranks without verifying an impact.

    Semantic matches require cosine similarity >= 0.35 and query-compatible
    dimensions. At most eight embedding records are inspected per request.
    Results copy content and citations from actual chunks, never record payloads.
    """
    if type(limit) is not int or not 0 <= limit <= 100:
        raise ValueError("Hybrid retrieval limit must be an integer between 0 and 100")
    if limit == 0:
        return []
    sources = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        content = chunk.get("content")
        if not allowed_path(chunk.get("path")) or not isinstance(content, str) or SECRET_PATTERN.search(content):
            continue
        sources.append(dict(chunk))
    by_identity = {_identity(source): source for source in sources}
    lexical = _lexical_rank(query, sources)
    query_vector = _vector(query_embedding)
    similarities: dict[tuple[Any, ...], float] = {}
    if query_vector:
        for record in islice(embedding_records, _MAX_VECTOR_RECORDS):
            if not isinstance(record, Mapping):
                continue
            vector = _vector(record.get("embedding"))
            if vector is None or len(vector) != len(query_vector):
                continue
            source = _matched_source(record, sources)
            if source is None:
                continue
            similarity = sum(left * right for left, right in zip(query_vector, vector, strict=True))
            if similarity >= _MIN_COSINE_SIMILARITY:
                identity = _identity(source)
                similarities[identity] = max(similarity, similarities.get(identity, -1.0))
    vector_ranking = sorted(similarities, key=lambda identity: (-similarities[identity], identity))
    lexical_ranks = {identity: rank for rank, identity in enumerate(lexical, 1)}
    vector_ranks = {identity: rank for rank, identity in enumerate(vector_ranking, 1)}
    ranked = []
    for identity in lexical_ranks.keys() | vector_ranks.keys():
        lexical_rank, vector_rank = lexical_ranks.get(identity), vector_ranks.get(identity)
        score = sum(1 / (_RRF_CONSTANT + rank) for rank in (lexical_rank, vector_rank) if rank is not None)
        source = dict(by_identity[identity])
        source.update({"snippet": source["content"][:4000], "citation": citation_for(source),
                       "score": round(score, 10), "role": "retrieval_context", "verified": False,
                       "evidence_status": "needs_investigation",
                       "retrieval": {"lexical_rank": lexical_rank, "vector_rank": vector_rank,
                                     "vector_similarity": similarities.get(identity)}})
        ranked.append(source)
    ranked.sort(key=lambda source: (-source["score"], _identity(source)))
    return ranked[:limit]

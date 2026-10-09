"""Deterministic, local retrieval over indexed source chunks.

Retrieval identifies useful context, not a verified dependency. Revision IDs and
line ranges are copied from the indexer, never inferred from token matches.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import log
import re
from typing import Any, Iterable, Mapping

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_STOP_WORDS = frozenset(
    "a an and are as at be by can const def do else for from function if in is "
    "it let of on or return that the this to var was were with".split()
)


def tokenize(value: str) -> list[str]:
    """Keep full identifiers and their snake/camel components for code search."""
    tokens: list[str] = []
    for match in _IDENTIFIER.finditer(value):
        identifier = match.group()
        full = identifier.lower()
        if full not in _STOP_WORDS:
            tokens.append(full)
        parts = _CAMEL_BOUNDARY.sub("_", identifier).lower().split("_")
        for part in dict.fromkeys(parts):
            if part and part != full and part not in _STOP_WORDS:
                tokens.append(part)
    return tokens


def citation_for(chunk: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a citation to the supplied snapshot, without inventing a SHA."""
    sha = chunk.get("commit_sha") or chunk.get("sha") or ""
    return {
        "path": chunk.get("path", ""),
        "start_line": chunk.get("start_line", 1),
        "end_line": chunk.get("end_line", chunk.get("start_line", 1)),
        "sha": sha,
        "commit_sha": sha,
        "symbol": chunk.get("symbol", ""),
        "kind": chunk.get("kind", ""),
    }


def search_chunks(
    query: str,
    chunks: Iterable[Mapping[str, Any]],
    *,
    limit: int = 8,
    max_snippet_chars: int = 4000,
) -> list[dict[str, Any]]:
    """Rank positive lexical matches, using stable file/line ordering for ties.

    Original content is preserved. ``snippet`` is bounded context for consumers
    such as optional model providers; its presence is not a verification claim.
    """
    if limit < 0 or max_snippet_chars < 1:
        raise ValueError("limit must be nonnegative and max_snippet_chars positive")
    query_tokens = set(tokenize(query))
    if not query_tokens or limit == 0:
        return []
    rows = [dict(chunk) for chunk in chunks]
    if not rows:
        return []
    fields = [
        (
            Counter(tokenize(str(row.get("symbol") or ""))),
            Counter(tokenize(str(row.get("path") or ""))),
            Counter(tokenize(str(row.get("content") or ""))),
        )
        for row in rows
    ]
    document_counts = Counter(
        token for fields_for_row in fields
        for token in set().union(*(set(field) for field in fields_for_row))
    )
    scored: list[dict[str, Any]] = []
    for row, row_fields in zip(rows, fields, strict=True):
        score = 0.0
        for token in sorted(query_tokens):
            frequency = sum(weight * field[token] for weight, field in zip((3, 2, 1), row_fields, strict=True))
            if frequency:
                inverse_frequency = 1 + log((len(rows) + 1) / (document_counts[token] + 1))
                score += inverse_frequency * (1 + log(frequency))
        if score > 0:
            row["score"] = round(score, 8)
            row["snippet"] = str(row.get("content") or "")[:max_snippet_chars]
            row["citation"] = citation_for(row)
            scored.append(row)
    scored.sort(
        key=lambda row: (
            -row["score"], str(row.get("path") or ""),
            int(row.get("start_line") or 1), int(row.get("end_line") or 1),
            str(row.get("symbol") or ""), str(row.get("content") or ""),
        )
    )
    return scored[:limit]


@dataclass(frozen=True)
class RetrievedDocument:
    """Small Document-compatible fallback when LangChain is not installed."""

    page_content: str
    metadata: dict[str, Any]


class LexicalRetriever:
    def __init__(self, chunks: Iterable[Mapping[str, Any]]) -> None:
        self.chunks = tuple(dict(chunk) for chunk in chunks)

    def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return search_chunks(query, self.chunks, **kwargs)

    def documents(self, query: str, **kwargs: Any) -> list[Any]:
        """Return LangChain Documents when available, with the same citations."""
        try:
            from langchain_core.documents import Document
        except ImportError:
            Document = RetrievedDocument
        return [
            Document(
                page_content=row["snippet"],
                metadata={**row["citation"], "score": row["score"]},
            )
            for row in self.search(query, **kwargs)
        ]

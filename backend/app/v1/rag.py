"""Provider-free, deterministic retrieval for explicit in-memory documents.

This Phase 2A module deliberately has no persistence, file-system access,
network access, model invocation, or Evidence integration.  It indexes only
documents supplied directly by its caller and emits citations that can be
recomputed from the immutable index snapshot.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..runtime import stable_hash

SCHEMA_VERSION = 1
RETRIEVER_VERSION = "deterministic-lexical-v1"
RANKER_VERSION = "integer-token-phrase-v1"
MAX_DOCUMENTS = 64
MAX_DOCUMENT_BYTES = 262_144
MAX_INDEX_BYTES = 1_048_576
MAX_QUERY_BYTES = 4_096
MAX_TOP_K = 20
MAX_CHUNK_CHARS = 800
MAX_METADATA_ITEMS = 16
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)
_SENSITIVE_METADATA_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "prompt",
    "secret",
    "token",
)


class RAGValidationError(ValueError):
    """A stable, input-safe rejection for RAG inputs and citations."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("deterministic rag rejected")


class _RAGModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL is not permitted")
    return value


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_PATTERN.findall(value.casefold()))


class RAGDocument(_RAGModel):
    """An explicit UTF-8 document supplied by the caller, never a file path."""

    id: str = Field(min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN)
    revision: int = Field(ge=1, le=1_000_000_000)
    text: str = Field(min_length=1, max_length=MAX_DOCUMENT_BYTES)
    metadata: dict[str, str] = Field(
        default_factory=dict, max_length=MAX_METADATA_ITEMS
    )

    @field_validator("text")
    @classmethod
    def normalize_document_text(cls, value: str) -> str:
        value = _reject_nul(value)
        value = _normalize_text(value)
        if not value.strip() or len(value.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise ValueError("document text must be non-empty and bounded")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, item in value.items():
            key_normalized = _normalize_text(_reject_nul(key))
            item_normalized = _normalize_text(_reject_nul(item))
            if (
                not key_normalized
                or len(key_normalized) > 80
                or len(item_normalized) > 1_000
                or any(
                    marker in key_normalized.casefold()
                    for marker in _SENSITIVE_METADATA_MARKERS
                )
            ):
                raise ValueError("metadata contains an unsafe key or value")
            if key_normalized in normalized:
                raise ValueError("metadata keys must remain unique after normalization")
            normalized[key_normalized] = item_normalized
        return dict(sorted(normalized.items()))

    @property
    def document_hash(self) -> str:
        return stable_hash(
            {
                "schema_version": SCHEMA_VERSION,
                "id": self.id,
                "revision": self.revision,
                "text": self.text,
                "metadata": self.metadata,
            }
        )


class RAGChunk(_RAGModel):
    id: str = Field(pattern=_IDENTIFIER_PATTERN)
    document_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    document_revision: int = Field(ge=1, le=1_000_000_000)
    document_hash: str = Field(pattern=_HASH_PATTERN)
    normalized_start: int = Field(ge=0)
    normalized_end: int = Field(gt=0)
    text_hash: str = Field(pattern=_HASH_PATTERN)
    tokens: tuple[str, ...] = Field(min_length=1)


class RAGCitation(_RAGModel):
    """A reference that is meaningful only when checked against an index/query."""

    index_hash: str = Field(pattern=_HASH_PATTERN)
    query_hash: str = Field(pattern=_HASH_PATTERN)
    chunk_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    document_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    document_revision: int = Field(ge=1, le=1_000_000_000)
    document_hash: str = Field(pattern=_HASH_PATTERN)
    normalized_start: int = Field(ge=0)
    normalized_end: int = Field(gt=0)
    text_hash: str = Field(pattern=_HASH_PATTERN)


class RAGHit(_RAGModel):
    score: int = Field(ge=1)
    citation: RAGCitation


class RAGSearchResult(_RAGModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    index_hash: str = Field(pattern=_HASH_PATTERN)
    query_hash: str = Field(pattern=_HASH_PATTERN)
    retriever_version: Literal["deterministic-lexical-v1"] = RETRIEVER_VERSION
    ranker_version: Literal["integer-token-phrase-v1"] = RANKER_VERSION
    requested_top_k: int = Field(ge=1, le=MAX_TOP_K)
    hits: tuple[RAGHit, ...]
    bundle_hash: str = Field(pattern=_HASH_PATTERN)


class RAGIndex(_RAGModel):
    """Canonical, immutable snapshot; its chunk text is held privately by caller code."""

    schema_version: Literal[1] = SCHEMA_VERSION
    index_hash: str = Field(pattern=_HASH_PATTERN)
    documents: tuple[RAGDocument, ...] = Field(min_length=1, max_length=MAX_DOCUMENTS)
    chunks: tuple[RAGChunk, ...] = Field(min_length=1)


def _parse_document(value: RAGDocument | Mapping[str, Any]) -> RAGDocument:
    if isinstance(value, RAGDocument):
        return value
    if not isinstance(value, Mapping):
        raise RAGValidationError("INVALID_DOCUMENT")
    try:
        return RAGDocument.model_validate(value)
    except ValidationError:
        raise RAGValidationError("INVALID_DOCUMENT") from None


def _split_normalized_text(text: str) -> tuple[tuple[int, int], ...]:
    """Split on code-point offsets, preferring whitespace at bounded intervals."""

    spans: list[tuple[int, int]] = []
    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + MAX_CHUNK_CHARS, text_length)
        if end < text_length:
            boundary = text.rfind(" ", start, end + 1)
            newline = text.rfind("\n", start, end + 1)
            boundary = max(boundary, newline)
            if boundary > start:
                end = boundary + 1
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
        start = max(end, start + 1)
    return tuple(spans)


def build_index(documents: Sequence[RAGDocument | Mapping[str, Any]]) -> RAGIndex:
    """Build an order-independent canonical index from bounded explicit input."""

    if isinstance(documents, (str, bytes)) or not isinstance(documents, Sequence):
        raise RAGValidationError("INVALID_DOCUMENTS")
    if not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise RAGValidationError("DOCUMENT_COUNT_OUT_OF_RANGE")
    parsed = tuple(_parse_document(value) for value in documents)
    if len({document.id for document in parsed}) != len(parsed):
        raise RAGValidationError("DUPLICATE_DOCUMENT_ID")
    if sum(len(document.text.encode("utf-8")) for document in parsed) > MAX_INDEX_BYTES:
        raise RAGValidationError("INDEX_BYTES_EXCEEDED")

    canonical_documents = tuple(sorted(parsed, key=lambda item: item.id))
    chunks: list[RAGChunk] = []
    for document in canonical_documents:
        document_hash = document.document_hash
        for ordinal, (start, end) in enumerate(
            _split_normalized_text(document.text), start=1
        ):
            chunk_text = document.text[start:end]
            token_values = _tokens(chunk_text)
            if not token_values:
                continue
            chunk_id = f"{document.id}:{ordinal}"
            chunks.append(
                RAGChunk(
                    id=chunk_id,
                    document_id=document.id,
                    document_revision=document.revision,
                    document_hash=document_hash,
                    normalized_start=start,
                    normalized_end=end,
                    text_hash=stable_hash(chunk_text),
                    tokens=token_values,
                )
            )
    if not chunks:
        raise RAGValidationError("NO_RETRIEVABLE_CONTENT")
    canonical_chunks = tuple(chunks)
    index_hash = stable_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "documents": [
                {
                    "id": document.id,
                    "revision": document.revision,
                    "document_hash": document.document_hash,
                }
                for document in canonical_documents
            ],
            "chunks": [chunk.model_dump(mode="json") for chunk in canonical_chunks],
        }
    )
    return RAGIndex(
        index_hash=index_hash,
        documents=canonical_documents,
        chunks=canonical_chunks,
    )


def _parse_query(query: str) -> tuple[str, tuple[str, ...], str]:
    if not isinstance(query, str):
        raise RAGValidationError("INVALID_QUERY")
    try:
        normalized = _normalize_text(_reject_nul(query)).strip()
    except ValueError:
        raise RAGValidationError("INVALID_QUERY") from None
    if not normalized or len(normalized.encode("utf-8")) > MAX_QUERY_BYTES:
        raise RAGValidationError("INVALID_QUERY")
    query_tokens = _tokens(normalized)
    if not query_tokens:
        raise RAGValidationError("NO_QUERY_TOKENS")
    return normalized, query_tokens, stable_hash(normalized)


def _require_top_k(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_TOP_K
    ):
        raise RAGValidationError("INVALID_TOP_K")
    return value


def _citation(index: RAGIndex, query_hash: str, chunk: RAGChunk) -> RAGCitation:
    return RAGCitation(
        index_hash=index.index_hash,
        query_hash=query_hash,
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_revision=chunk.document_revision,
        document_hash=chunk.document_hash,
        normalized_start=chunk.normalized_start,
        normalized_end=chunk.normalized_end,
        text_hash=chunk.text_hash,
    )


def _require_canonical_index(index: RAGIndex) -> None:
    """Reject a hand-constructed or stale snapshot before retrieval/citation checks."""

    expected = build_index(index.documents)
    if (
        index.index_hash != expected.index_hash
        or index.documents != expected.documents
        or index.chunks != expected.chunks
    ):
        raise RAGValidationError("INVALID_INDEX")


def _bundle_hash(
    *, index_hash: str, query_hash: str, requested_top_k: int, hits: tuple[RAGHit, ...]
) -> str:
    """Hash the full ordered retrieval result, including algorithm bindings."""

    return stable_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "index_hash": index_hash,
            "query_hash": query_hash,
            "retriever_version": RETRIEVER_VERSION,
            "ranker_version": RANKER_VERSION,
            "requested_top_k": requested_top_k,
            "hits": [hit.model_dump(mode="json") for hit in hits],
        }
    )


def search(index: RAGIndex, query: str, top_k: int = 5) -> RAGSearchResult:
    """Return deterministic lexical matches, not an answer or an Evidence record."""

    if not isinstance(index, RAGIndex):
        raise RAGValidationError("INVALID_INDEX")
    _require_canonical_index(index)
    normalized_query, query_tokens, query_hash = _parse_query(query)
    top_k = _require_top_k(top_k)
    query_counts = Counter(query_tokens)
    query_folded = normalized_query.casefold()
    scored: list[tuple[int, RAGChunk]] = []
    for chunk in index.chunks:
        token_counts = Counter(chunk.tokens)
        score = sum(
            min(query_count, token_counts[token]) * 10
            for token, query_count in query_counts.items()
            if token in token_counts
        )
        # The phrase bonus stays integral and only applies to a normalized query.
        if query_folded in _chunk_text(index, chunk).casefold():
            score += 100
        if score:
            scored.append((score, chunk))
    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].document_id,
            item[1].normalized_start,
            item[1].id,
        )
    )
    hits = tuple(
        RAGHit(score=score, citation=_citation(index, query_hash, chunk))
        for score, chunk in scored[:top_k]
    )
    return RAGSearchResult(
        index_hash=index.index_hash,
        query_hash=query_hash,
        retriever_version=RETRIEVER_VERSION,
        ranker_version=RANKER_VERSION,
        requested_top_k=top_k,
        hits=hits,
        bundle_hash=_bundle_hash(
            index_hash=index.index_hash,
            query_hash=query_hash,
            requested_top_k=top_k,
            hits=hits,
        ),
    )


def _chunk_text(index: RAGIndex, chunk: RAGChunk) -> str:
    document = next(
        (
            candidate
            for candidate in index.documents
            if candidate.id == chunk.document_id
        ),
        None,
    )
    if document is None:
        raise RAGValidationError("INVALID_INDEX")
    return document.text[chunk.normalized_start : chunk.normalized_end]


def verify_citation(
    index: RAGIndex, query: str, citation: RAGCitation | Mapping[str, Any]
) -> bool:
    """Independently recompute a citation; never trust caller-provided fields."""

    if not isinstance(index, RAGIndex):
        raise RAGValidationError("INVALID_INDEX")
    _require_canonical_index(index)
    _, _, query_hash = _parse_query(query)
    try:
        parsed = (
            citation
            if isinstance(citation, RAGCitation)
            else RAGCitation.model_validate(citation)
        )
    except ValidationError:
        return False
    if parsed.index_hash != index.index_hash or parsed.query_hash != query_hash:
        return False
    chunk = next(
        (candidate for candidate in index.chunks if candidate.id == parsed.chunk_id),
        None,
    )
    if chunk is None:
        return False
    expected = _citation(index, query_hash, chunk)
    return (
        parsed == expected and stable_hash(_chunk_text(index, chunk)) == chunk.text_hash
    )


def verify_search_result(
    index: RAGIndex,
    query: str,
    result: RAGSearchResult | Mapping[str, Any],
) -> bool:
    """Reject every caller-supplied field against the result's own top-k binding."""

    try:
        parsed = (
            result
            if isinstance(result, RAGSearchResult)
            else RAGSearchResult.model_validate(result)
        )
    except ValidationError:
        return False
    try:
        expected = search(index, query, top_k=parsed.requested_top_k)
    except RAGValidationError:
        return False
    if parsed != expected:
        return False
    return parsed.bundle_hash == _bundle_hash(
        index_hash=parsed.index_hash,
        query_hash=parsed.query_hash,
        requested_top_k=parsed.requested_top_k,
        hits=parsed.hits,
    )


__all__ = [
    "MAX_CHUNK_CHARS",
    "MAX_DOCUMENTS",
    "MAX_QUERY_BYTES",
    "RANKER_VERSION",
    "RETRIEVER_VERSION",
    "RAGChunk",
    "RAGCitation",
    "RAGDocument",
    "RAGHit",
    "RAGIndex",
    "RAGSearchResult",
    "RAGValidationError",
    "build_index",
    "search",
    "verify_citation",
    "verify_search_result",
]

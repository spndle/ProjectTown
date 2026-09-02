from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.v1.rag import (
    RANKER_VERSION,
    RETRIEVER_VERSION,
    RAGCitation,
    RAGDocument,
    RAGValidationError,
    build_index,
    search,
    verify_citation,
    verify_search_result,
)


def _documents() -> list[dict[str, object]]:
    return [
        {
            "id": "alpha",
            "revision": 2,
            "text": "ProjectTown uses deterministic retrieval. 中文检索必须可复现。",
            "metadata": {"source": "fixture"},
        },
        {
            "id": "beta",
            "revision": 1,
            "text": "Noise document about unrelated gardening and weather.",
            "metadata": {},
        },
        {
            "id": "gamma",
            "revision": 1,
            "text": "确定性 检索 需要 稳定 排序 和 可验证 引用。",
            "metadata": {},
        },
    ]


def test_index_is_order_independent_and_rebuilds_identically() -> None:
    first = build_index(_documents())
    second = build_index(list(reversed(_documents())))
    third = build_index(_documents())
    assert first.index_hash == second.index_hash == third.index_hash
    assert first.chunks == second.chunks == third.chunks
    assert search(first, "deterministic retrieval") == search(
        second, "deterministic retrieval"
    )


def test_english_chinese_retrieval_and_noise_exclusion() -> None:
    index = build_index(_documents())
    english = search(index, "deterministic retrieval")
    chinese = search(index, "确定性检索")
    assert english.hits[0].citation.document_id == "alpha"
    assert chinese.hits[0].citation.document_id == "gamma"
    assert all(
        hit.citation.document_id != "beta" for hit in english.hits + chinese.hits
    )
    assert search(index, "absent vocabulary").hits == ()


def test_stable_tie_break_and_integer_score() -> None:
    index = build_index(
        [
            {"id": "zeta", "revision": 1, "text": "same token", "metadata": {}},
            {"id": "alpha", "revision": 1, "text": "same token", "metadata": {}},
        ]
    )
    result = search(index, "token", top_k=2)
    assert [hit.citation.document_id for hit in result.hits] == ["alpha", "zeta"]
    assert all(isinstance(hit.score, int) for hit in result.hits)


def test_citation_is_recomputable_and_detects_tampering() -> None:
    index = build_index(_documents())
    result = search(index, "deterministic retrieval")
    citation = result.hits[0].citation
    assert verify_citation(index, "deterministic retrieval", citation)
    assert not verify_citation(index, "different query", citation)
    assert not verify_citation(
        index,
        "deterministic retrieval",
        citation.model_copy(update={"normalized_end": citation.normalized_end - 1}),
    )
    assert not verify_citation(
        index,
        "deterministic retrieval",
        citation.model_copy(update={"document_hash": "0" * 64}),
    )
    assert not verify_citation(
        index, "deterministic retrieval", {"chunk_id": citation.chunk_id}
    )


def test_result_bundle_is_complete_recomputable_and_tamper_evident() -> None:
    index = build_index(_documents())
    result = search(index, "deterministic retrieval")
    assert result.retriever_version == RETRIEVER_VERSION
    assert result.ranker_version == RANKER_VERSION
    assert result.requested_top_k == 5
    assert len(result.bundle_hash) == 64
    assert verify_search_result(index, "deterministic retrieval", result)
    assert not verify_search_result(
        index,
        "deterministic retrieval",
        result.model_copy(update={"retriever_version": "other"}),
    )
    assert not verify_search_result(
        index,
        "deterministic retrieval",
        result.model_copy(update={"ranker_version": "other"}),
    )
    ordered_result = search(index, "检索")
    assert len(ordered_result.hits) >= 2
    assert not verify_search_result(
        index,
        "检索",
        ordered_result.model_copy(
            update={"hits": tuple(reversed(ordered_result.hits))}
        ),
    )
    changed_hit = result.hits[0].model_copy(update={"score": result.hits[0].score + 1})
    assert not verify_search_result(
        index,
        "deterministic retrieval",
        result.model_copy(update={"hits": (changed_hit, *result.hits[1:])}),
    )
    changed_citation = result.hits[0].citation.model_copy(
        update={"document_hash": "0" * 64}
    )
    changed_citation_hit = result.hits[0].model_copy(
        update={"citation": changed_citation}
    )
    assert not verify_search_result(
        index,
        "deterministic retrieval",
        result.model_copy(update={"hits": (changed_citation_hit, *result.hits[1:])}),
    )
    assert not verify_search_result(
        index,
        "deterministic retrieval",
        result.model_copy(update={"bundle_hash": "0" * 64}),
    )
    assert not verify_search_result(
        index,
        "deterministic retrieval",
        result.model_copy(update={"requested_top_k": 1}),
    )


def test_requested_top_k_is_self_describing_even_when_result_has_fewer_hits() -> None:
    index = build_index(_documents())
    top_one = search(index, "检索", top_k=1)
    assert len(top_one.hits) == 1
    assert top_one.requested_top_k == 1
    assert verify_search_result(index, "检索", top_one)

    fewer_hits = search(index, "deterministic retrieval", top_k=7)
    assert len(fewer_hits.hits) == 1
    assert fewer_hits.requested_top_k == 7
    assert verify_search_result(index, "deterministic retrieval", fewer_hits)


def test_no_answer_bundle_is_recomputable() -> None:
    index = build_index(_documents())
    result = search(index, "absent vocabulary")
    assert result.hits == ()
    assert result.requested_top_k == 5
    assert verify_search_result(index, "absent vocabulary", result)
    assert not verify_search_result(
        index,
        "absent vocabulary",
        result.model_copy(update={"bundle_hash": "f" * 64}),
    )


@pytest.mark.parametrize(
    ("documents", "code"),
    [
        (
            [
                {"id": "same", "revision": 1, "text": "a", "metadata": {}},
                {"id": "same", "revision": 1, "text": "b", "metadata": {}},
            ],
            "DUPLICATE_DOCUMENT_ID",
        ),
        (
            [{"id": "nul", "revision": 1, "text": "a\x00b", "metadata": {}}],
            "INVALID_DOCUMENT",
        ),
        (
            [
                {
                    "id": "secret",
                    "revision": 1,
                    "text": "text",
                    "metadata": {"api_key": "no"},
                }
            ],
            "INVALID_DOCUMENT",
        ),
        (
            [{"id": "empty", "revision": 1, "text": "   ", "metadata": {}}],
            "INVALID_DOCUMENT",
        ),
    ],
)
def test_untrusted_document_inputs_are_rejected(
    documents: list[dict[str, object]], code: str
) -> None:
    with pytest.raises(RAGValidationError) as raised:
        build_index(documents)
    assert raised.value.code == code
    assert "secret" not in str(raised.value).lower()


def test_strict_models_and_query_bounds() -> None:
    with pytest.raises(ValidationError):
        RAGDocument.model_validate(
            {"id": "one", "revision": 1, "text": "text", "metadata": {}, "extra": "no"}
        )
    index = build_index(_documents())
    for query in ("", "\x00", "x" * 4_097):
        with pytest.raises(RAGValidationError):
            search(index, query)
    for top_k in (0, 21, True):
        with pytest.raises(RAGValidationError):
            search(index, "query", top_k=top_k)  # type: ignore[arg-type]


def test_normalized_offsets_are_against_nfc_text() -> None:
    index = build_index(
        [
            {
                "id": "unicode",
                "revision": 1,
                "text": "Cafe\u0301 retrieval",
                "metadata": {},
            }
        ]
    )
    citation = search(index, "café").hits[0].citation
    document = index.documents[0]
    assert document.text == "Café retrieval"
    assert (
        document.text[citation.normalized_start : citation.normalized_end]
        == "Café retrieval"
    )
    assert verify_citation(index, "café", citation)


def test_citation_contract_is_strict() -> None:
    with pytest.raises(ValidationError):
        RAGCitation.model_validate(
            {
                "index_hash": "0" * 64,
                "query_hash": "0" * 64,
                "chunk_id": "one:1",
                "document_id": "one",
                "document_revision": 1,
                "document_hash": "0" * 64,
                "normalized_start": 0,
                "normalized_end": 1,
                "text_hash": "0" * 64,
                "extra": "no",
            }
        )


def test_hand_constructed_stale_index_is_not_trusted() -> None:
    index = build_index(_documents())
    stale = index.model_copy(update={"index_hash": "0" * 64})
    with pytest.raises(RAGValidationError) as raised:
        search(stale, "deterministic")
    assert raised.value.code == "INVALID_INDEX"

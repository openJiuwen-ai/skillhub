# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Search logging should expose existing scores without changing retrieval behavior."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from indexing.bm25.index import build_bm25_index
from indexing.embedding.index import EmbeddingIndex, IndexedEmbeddingRecord
from models.retrieval import FinderNode
from plugins_market.core.config import Settings
from plugins_market.retrieval import index_manager as manager_module
from retrieval.io.loading import CatalogRecord, LoadedFinderIndex
from retrieval.lexical.bm25 import BM25Document, BM25Finder, BM25FinderConfig
from retrieval.service.retriever import Retriever


def configured(**values):
    with patch.dict(os.environ, values, clear=True):
        return Settings(_env_file=None)


class FixedEmbedding:
    def __init__(self):
        self.calls = 0

    def embed_text(self, text):
        self.calls += 1
        return [1.0, 0.0]


def make_retriever():
    client = FixedEmbedding()
    vectors = EmbeddingIndex(model_name="test", dimensions=2, records=tuple(
        IndexedEmbeddingRecord(choice_id=cid, payload=cid, text="PRIVATE_DOCUMENT_BODY", vector=vector)
        for cid, vector in [("a", (0.96, 0.28)), ("b", (0.8, 0.6)), ("c", (0.6, 0.8))]
    ))
    bm25 = build_bm25_index(documents=[
        BM25Document(choice_id="a", payload="a", text="needle " + "hay " * 30),
        BM25Document(choice_id="b", payload="b", text="needle " * 6),
        BM25Document(choice_id="c", payload="c", text="hay"),
        BM25Document(choice_id="d", payload="d", text="needle " + "hay " * 60),
    ])
    catalog_records = tuple(
        CatalogRecord(choice_id=cid, payload=cid, metadata={"skill_path": f"s3://test/skills/owner/asset-{cid}/1.0.0/"})
        for cid in "abcd"
    )
    loaded = LoadedFinderIndex(
        index_dir=Path("test-index"), tree_root=FinderNode("ROOT", "ROOT"), choices=(),
        catalog_records=catalog_records, embedding_index=vectors, bm25_index=bm25,
    )
    return Retriever(loaded_index=loaded, embedding_client=client), client


class RetrievalLoggingTests(unittest.TestCase):
    def setUp(self):
        self.retriever, self.client = make_retriever()
        self.manager = manager_module.IndexManager()
        with patch.object(Retriever, "from_index", return_value=self.retriever):
            self.manager.load("skill", "test-index")
        self.assertTrue(self.manager.is_ready("skill"))

    def search(self, *, top_k=4, **values):
        settings = configured(**values)
        with patch.object(manager_module, "settings", settings), patch.object(manager_module, "logger") as logger:
            result = self.manager.search("skill", "needle\nsecond", top_k)
        events = [(call.args[0], call.kwargs) for call in logger.info.call_args_list]
        stages = {data["stage"]: json.loads(data["details"]) for name, data in events if name == "retrieval_stage"}
        return result, events, stages

    def test_logs_actual_threshold_and_rejected_scores(self):
        result, events, stages = self.search(MARKET_RETRIEVAL_EMBEDDING_MIN_SCORE="0.7",
                                             MARKET_RETRIEVAL_BM25_MIN_SCORE="1000")
        self.assertEqual(result, ["asset-a"])
        self.assertEqual(self.client.calls, 1)
        self.assertEqual(events[0][0], "retrieval_params")
        self.assertEqual(json.loads(events[0][1]["details"])["embedding_min_score"], 0.7)
        vector = stages["embedding_retrieval"]
        self.assertAlmostEqual(vector["effective_min_score"], 0.96 * 0.9)
        self.assertEqual(vector["score_filter_removed_count"], 2)
        self.assertEqual([hit["score"] for hit in vector["truncation"]["score_rejected_samples"]], [0.8, 0.6])
        self.assertEqual(stages["bm25_retrieval"]["score_filter_removed_count"], 3)
        for _, data in events:
            self.assertNotIn("\n", data.get("keyword", ""))
            self.assertNotIn("\n", data.get("details", ""))
            self.assertNotIn("PRIVATE_DOCUMENT_BODY", str(data))

    def test_top_k_is_separate_from_score_filtering(self):
        _, _, stages = self.search(top_k=1, MARKET_RETRIEVAL_EMBEDDING_RELATIVE_MIN_SCORE="0")
        bm25 = stages["bm25_retrieval"]
        self.assertEqual(bm25["score_filter_removed_count"], 0)
        self.assertEqual(bm25["top_k_removed_count"], 2)

    def test_fused_order_and_empty_results_are_unchanged(self):
        result, events, _ = self.search(MARKET_RETRIEVAL_EMBEDDING_RELATIVE_MIN_SCORE="0")
        self.assertEqual(result, ["asset-a", "asset-b", "asset-c", "asset-d"])
        final = next(data for name, data in events if name == "retrieval_result")
        records = json.loads(final["details"])
        self.assertEqual([row["asset_id"] for row in records], result)
        self.assertEqual(records[0]["source_ranks"], {"embedding": 1, "bm25": 2})
        self.assertIsNotNone(records[0]["fusion_score"])
        result, events, _ = self.search(MARKET_RETRIEVAL_EMBEDDING_MIN_SCORE="1",
                                       MARKET_RETRIEVAL_BM25_MIN_SCORE="1000")
        self.assertEqual(result, [])
        self.assertEqual(events[-1][1]["asset_ids"], [])

    def test_rejected_samples_are_bounded_and_term_filter_is_counted(self):
        docs = [BM25Document(str(i), str(i), "needle") for i in range(25)]
        filtered = BM25Finder(documents=docs, config=BM25FinderConfig(min_score=1000)).retrieve_top_k(query="needle")
        self.assertEqual(filtered.hits, [])
        self.assertEqual(len(filtered.truncation["score_rejected_samples"]), 10)
        self.assertEqual(filtered.truncation["score_rejected_omitted_count"], 15)
        matched = BM25Finder(
            documents=docs, config=BM25FinderConfig(min_query_term_matches=2),
        ).retrieve_top_k(query="needle cat")
        self.assertEqual(matched.truncation["term_match_rejected_count"], 25)
        self.assertEqual(matched.truncation["query_terms"], ["needle", "cat"])

    def test_model_error_preserves_fallback(self):
        with patch.object(self.client, "embed_text", side_effect=RuntimeError("test timeout")):
            result, _, _ = self.search()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

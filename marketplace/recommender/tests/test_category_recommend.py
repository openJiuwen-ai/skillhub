"""Unit tests for category-aware recommend / milvus index planning.

Run:
  cd marketplace && python -m unittest recommender.tests.test_category_recommend -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Ensure marketplace/ is on path when run as script or module.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recommender.offline.milvus_index.planner import (
    category_needs_update,
    content_needs_reindex,
    plan_full,
    plan_incremental,
)
from recommender.offline.milvus_index.state import IndexedAsset, IndexState, load_state, replace_state
from recommender.offline.package_sync.db import ActiveSkillVersion
from recommender.online.mmr import mmr_rerank
from recommender.online.redis_seeds import load_topk_install_items
from recommender.online.search import (
    _category_expr,
    _plugin_type_expr,
    _search_expr,
    merge_max_score,
    parse_plugin_types,
    search_vectors,
)
from recommender.online.service import (
    SOURCE_TOPK_INSTALL,
    SOURCE_USER_HISTORY,
    recommend_by_ids,
    recommend_for_user,
)
from recommender.online.types import RecommendItem


def _skill(
    asset_id: str,
    *,
    version: str = "1.0.0",
    sha: str | None = "abc",
    category_id: str = "",
    plugin_type: str = "skill",
) -> ActiveSkillVersion:
    return ActiveSkillVersion(
        asset_id=asset_id,
        name=asset_id,
        display_name=asset_id,
        short_desc="desc",
        plugin_type=plugin_type,
        status="ONLINE",
        latest_version=version,
        version_id=f"v-{asset_id}",
        file_path=f"skills/{asset_id}",
        artifact_sha256=sha,
        category_id=category_id,
    )


class TestPlannerCategory(unittest.TestCase):
    def test_content_change_triggers_upsert(self) -> None:
        skill = _skill("a1", version="2.0.0", category_id="software-development")
        indexed = IndexedAsset(
            version="1.0.0",
            artifact_sha256="abc",
            indexed_at="t",
            category_id="software-development",
        )
        self.assertTrue(content_needs_reindex(skill, indexed))
        self.assertFalse(category_needs_update(skill, indexed))

    def test_category_only_change(self) -> None:
        skill = _skill("a1", category_id="office-productivity")
        indexed = IndexedAsset(
            version="1.0.0",
            artifact_sha256="abc",
            indexed_at="t",
            category_id="software-development",
        )
        self.assertFalse(content_needs_reindex(skill, indexed))
        self.assertTrue(category_needs_update(skill, indexed))

    def test_empty_category_normalized(self) -> None:
        skill = _skill("a1", category_id="")
        indexed = IndexedAsset(
            version="1.0.0",
            artifact_sha256="abc",
            indexed_at="t",
            category_id="",
        )
        self.assertFalse(category_needs_update(skill, indexed))
        skill2 = _skill("a1", category_id="  software-development  ")
        self.assertEqual(skill2.normalized_category_id, "software-development")
        self.assertTrue(category_needs_update(skill2, indexed))

    def test_plan_incremental_splits_paths(self) -> None:
        state = IndexState(
            updated_at="t",
            assets={
                "keep": IndexedAsset("1.0.0", "abc", "t", "software-development", "skill"),
                "cat": IndexedAsset("1.0.0", "abc", "t", "", "skill"),
                "gone": IndexedAsset("1.0.0", "abc", "t", "", "skill"),
            },
        )
        active = [
            _skill("keep", category_id="software-development"),
            _skill("cat", category_id="software-development"),
            _skill("new", category_id="finance-wealth"),
            _skill("ver", version="2.0.0", category_id=""),
        ]
        # ver not in state -> content upsert; also add ver to state as old for content path
        state.assets["ver"] = IndexedAsset("1.0.0", "abc", "t", "", "skill")

        plan = plan_incremental(active, state)
        upsert_ids = {s.asset_id for s in plan.to_upsert}
        cat_ids = {s.asset_id for s in plan.category_only}
        self.assertIn("new", upsert_ids)
        self.assertIn("ver", upsert_ids)
        self.assertIn("cat", cat_ids)
        self.assertNotIn("keep", upsert_ids)
        self.assertNotIn("keep", cat_ids)
        self.assertEqual(plan.to_delete, ["gone"])

    def test_plan_full_all_upsert(self) -> None:
        active = [_skill("a"), _skill("b", category_id="x")]
        plan = plan_full(active)
        self.assertEqual(len(plan.to_upsert), 2)
        self.assertEqual(plan.category_only, [])
        self.assertEqual(plan.to_delete, [])


class TestPlannerPluginType(unittest.TestCase):
    def test_plugin_type_only_change_reuses_embedding_path(self) -> None:
        from recommender.offline.milvus_index.planner import plugin_type_needs_update, scalars_need_update

        skill = _skill("a1", plugin_type="swarmskill")
        indexed = IndexedAsset(
            version="1.0.0",
            artifact_sha256="abc",
            indexed_at="t",
            category_id="",
            plugin_type="skill",
        )
        self.assertFalse(content_needs_reindex(skill, indexed))
        self.assertTrue(plugin_type_needs_update(skill, indexed))
        self.assertTrue(scalars_need_update(skill, indexed))

        state = IndexState(updated_at="t", assets={"a1": indexed})
        plan = plan_incremental([skill], state)
        self.assertEqual([s.asset_id for s in plan.category_only], ["a1"])
        self.assertEqual(plan.to_upsert, [])

    def test_teamskills_alias_does_not_trigger_update(self) -> None:
        from recommender.offline.milvus_index.planner import plugin_type_needs_update

        skill = _skill("a1", plugin_type="teamskills")
        indexed = IndexedAsset("1.0.0", "abc", "t", "", "swarmskill")
        self.assertFalse(plugin_type_needs_update(skill, indexed))


class TestStateCategory(unittest.TestCase):
    def test_roundtrip_category_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            replace_state(
                {
                    "a1": IndexedAsset("1.0.0", "sha", "t0", "software-development"),
                    "a2": IndexedAsset("1.0.0", None, "t0", ""),
                },
                path,
            )
            loaded = load_state(path)
            self.assertEqual(loaded.get("a1").category_id, "software-development")
            self.assertEqual(loaded.get("a2").category_id, "")

    def test_legacy_state_without_category_defaults_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "updated_at": "t",
                        "assets": {
                            "a1": {
                                "version": "1.0.0",
                                "artifact_sha256": "sha",
                                "indexed_at": "t",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_state(path)
            self.assertEqual(loaded.get("a1").category_id, "")
            self.assertEqual(loaded.get("a1").plugin_type, "")


class TestSearchCategoryExpr(unittest.TestCase):
    def test_category_expr_empty(self) -> None:
        self.assertIsNone(_category_expr(None))
        self.assertIsNone(_category_expr(""))
        self.assertIsNone(_category_expr("  "))

    def test_category_expr_value(self) -> None:
        self.assertEqual(
            _category_expr("software-development"),
            'category_id == "software-development"',
        )

    def test_category_expr_escapes_quotes(self) -> None:
        self.assertEqual(_category_expr('a"b'), 'category_id == "a\\"b"')


class TestSearchPluginTypeExpr(unittest.TestCase):
    def test_parse_plugin_types(self) -> None:
        self.assertEqual(parse_plugin_types(""), [])
        self.assertEqual(parse_plugin_types("skill"), ["skill"])
        self.assertEqual(parse_plugin_types("teamskills"), ["swarmskill"])
        self.assertEqual(parse_plugin_types("skill, swarmskill"), ["skill", "swarmskill"])

    def test_plugin_type_expr_single(self) -> None:
        self.assertEqual(_plugin_type_expr("skill"), 'plugin_type == "skill"')
        self.assertIsNone(_plugin_type_expr(""))

    def test_plugin_type_expr_multi(self) -> None:
        self.assertEqual(
            _plugin_type_expr("skill,swarmskill"),
            'plugin_type in ["skill", "swarmskill"]',
        )

    def test_search_expr_combines(self) -> None:
        self.assertEqual(
            _search_expr(category_id="software-development", plugin_type="skill"),
            'category_id == "software-development" and plugin_type == "skill"',
        )

    def test_search_vectors_passes_plugin_type_expr(self) -> None:
        collection = MagicMock()
        hit = SimpleNamespace(distance=0.9, entity={"asset_id": "x1"}, id="x1")
        collection.search.return_value = [[hit]]
        search_vectors(
            collection,
            [[0.1, 0.2]],
            top_k=5,
            plugin_type="swarmskill",
        )
        kwargs = collection.search.call_args.kwargs
        self.assertEqual(kwargs["expr"], 'plugin_type == "swarmskill"')

    def test_search_vectors_no_expr_when_empty_plugin_type(self) -> None:
        collection = MagicMock()
        collection.search.return_value = [[]]
        search_vectors(collection, [[0.1]], top_k=3, plugin_type="")
        kwargs = collection.search.call_args.kwargs
        self.assertNotIn("expr", kwargs)

    def test_search_vectors_passes_expr(self) -> None:
        collection = MagicMock()
        hit = SimpleNamespace(distance=0.9, entity={"asset_id": "x1"}, id="x1")
        collection.search.return_value = [[hit]]
        out = search_vectors(
            collection,
            [[0.1, 0.2]],
            top_k=5,
            category_id="software-development",
        )
        self.assertEqual(out, [[("x1", 0.9)]])
        kwargs = collection.search.call_args.kwargs
        self.assertEqual(kwargs["expr"], 'category_id == "software-development"')
        self.assertEqual(kwargs["limit"], 5)

    def test_search_vectors_no_expr_when_empty_category(self) -> None:
        collection = MagicMock()
        collection.search.return_value = [[]]
        search_vectors(collection, [[0.1]], top_k=3, category_id="")
        kwargs = collection.search.call_args.kwargs
        self.assertNotIn("expr", kwargs)

    def test_merge_max_score_exclude(self) -> None:
        hits = [[("a", 0.9), ("b", 0.8)], [("b", 0.95), ("c", 0.7)]]
        items = merge_max_score(hits, exclude_ids={"a"}, top_k=2)
        self.assertEqual([i.asset_id for i in items], ["b", "c"])
        self.assertAlmostEqual(items[0].score, 0.95)


class TestRedisTopkCategory(unittest.TestCase):
    def _patch_redis(self, items: list[dict]):
        client = MagicMock()
        client.get.return_value = json.dumps({"items": items})
        cfg = SimpleNamespace(topk_install=SimpleNamespace(key="k"))
        return patch(
            "recommender.online.redis_seeds._redis",
            return_value=(client, cfg),
        )

    def test_filter_by_category(self) -> None:
        items = [
            {"asset_id": "a", "rank": 1, "category_id": "software-development"},
            {"asset_id": "b", "rank": 2, "category_id": "office-productivity"},
            {"asset_id": "c", "rank": 3, "category_id": "software-development"},
            {"asset_id": "d", "rank": 4},  # missing category -> excluded when filtering
        ]
        with self._patch_redis(items):
            out = load_topk_install_items(0, category_id="software-development")
        self.assertEqual([x.asset_id for x in out], ["a", "c"])

    def test_no_category_returns_all(self) -> None:
        items = [
            {"asset_id": "a", "rank": 1, "category_id": "software-development"},
            {"asset_id": "b", "rank": 2, "category_id": "office-productivity"},
        ]
        with self._patch_redis(items):
            out = load_topk_install_items(0, category_id="")
        self.assertEqual([x.asset_id for x in out], ["a", "b"])

    def test_exclude_ids(self) -> None:
        items = [
            {"asset_id": "a", "rank": 1, "category_id": "software-development"},
            {"asset_id": "b", "rank": 2, "category_id": "software-development"},
        ]
        with self._patch_redis(items):
            out = load_topk_install_items(
                0,
                exclude_ids={"a"},
                category_id="software-development",
            )
        self.assertEqual([x.asset_id for x in out], ["b"])

    def test_filter_plugin_type(self) -> None:
        items = [
            {"asset_id": "a", "rank": 1, "plugin_type": "skill"},
            {"asset_id": "b", "rank": 2, "plugin_type": "swarmskill"},
            {"asset_id": "c", "rank": 3, "plugin_type": "skill"},
        ]
        with self._patch_redis(items):
            out = load_topk_install_items(0, plugin_type="skill")
        self.assertEqual([x.asset_id for x in out], ["a", "c"])

    def test_plugin_type_fills_limit_from_mixed_pool(self) -> None:
        items = [
            {"asset_id": "a", "rank": 1, "plugin_type": "skill"},
            {"asset_id": "b", "rank": 2, "plugin_type": "swarmskill"},
            {"asset_id": "c", "rank": 3, "plugin_type": "swarmskill"},
            {"asset_id": "d", "rank": 4, "plugin_type": "skill"},
        ]
        with self._patch_redis(items):
            out = load_topk_install_items(2, plugin_type="swarmskill")
        self.assertEqual([x.asset_id for x in out], ["b", "c"])

    def test_missing_key_returns_empty(self) -> None:
        client = MagicMock()
        client.get.return_value = None
        cfg = SimpleNamespace(topk_install=SimpleNamespace(key="k"))
        with patch("recommender.online.redis_seeds._redis", return_value=(client, cfg)):
            self.assertEqual(load_topk_install_items(0, category_id="x"), [])


class TestRecommendForUserCategory(unittest.TestCase):
    def test_history_path_passes_category(self) -> None:
        with (
            patch("recommender.online.service.load_user_seed_ids", return_value=["s1", "s2"]),
            patch("recommender.online.service.get_loaded_collection", return_value=object()),
            patch(
                "recommender.online.service.recommend_by_ids",
                return_value=[RecommendItem("r1", 0.9), RecommendItem("r2", 0.8)],
            ) as by_ids,
            patch(
                "recommender.online.service.rerank_mmr",
                return_value=[RecommendItem("r1", 0.9)],
            ) as mmr,
        ):
            items, source = recommend_for_user(
                user_id="u1",
                top_k=5,
                category_id="software-development",
            )
        self.assertEqual(source, SOURCE_USER_HISTORY)
        self.assertEqual([i.asset_id for i in items], ["r1"])
        self.assertEqual(by_ids.call_args.kwargs["category_id"], "software-development")
        mmr.assert_called_once()

    def test_history_path_passes_plugin_type(self) -> None:
        with (
            patch("recommender.online.service.load_user_seed_ids", return_value=["s1"]),
            patch("recommender.online.service.get_loaded_collection", return_value=object()),
            patch(
                "recommender.online.service.recommend_by_ids",
                return_value=[RecommendItem("r1", 0.9)],
            ) as by_ids,
            patch(
                "recommender.online.service.rerank_mmr",
                return_value=[RecommendItem("r1", 0.9)],
            ),
        ):
            recommend_for_user(user_id="u1", top_k=5, plugin_type="swarmskill")
        self.assertEqual(by_ids.call_args.kwargs["plugin_type"], "swarmskill")

    def test_no_user_goes_topk_with_plugin_type(self) -> None:
        with patch(
            "recommender.online.service.load_topk_install_items",
            return_value=[RecommendItem("t1", 1.0)],
        ) as topk:
            items, source = recommend_for_user(user_id="", top_k=3, plugin_type="skill")
        self.assertEqual(source, SOURCE_TOPK_INSTALL)
        self.assertEqual(topk.call_args.kwargs["plugin_type"], "skill")
        self.assertEqual(len(items), 1)

    def test_history_empty_falls_back_topk_with_category(self) -> None:
        with (
            patch("recommender.online.service.load_user_seed_ids", return_value=["s1"]),
            patch("recommender.online.service.get_loaded_collection", return_value=object()),
            patch("recommender.online.service.recommend_by_ids", return_value=[]),
            patch(
                "recommender.online.service.load_topk_install_items",
                return_value=[RecommendItem("t1", 1.0)],
            ) as topk,
        ):
            items, source = recommend_for_user(
                user_id="u1",
                top_k=5,
                category_id="office-productivity",
            )
        self.assertEqual(source, SOURCE_TOPK_INSTALL)
        self.assertEqual(items[0].asset_id, "t1")
        self.assertEqual(topk.call_args.args[0], 5)
        self.assertEqual(topk.call_args.kwargs["category_id"], "office-productivity")

    def test_no_user_goes_topk(self) -> None:
        with patch(
            "recommender.online.service.load_topk_install_items",
            return_value=[RecommendItem("t1", 1.0)],
        ) as topk:
            items, source = recommend_for_user(user_id="", top_k=3, category_id="")
        self.assertEqual(source, SOURCE_TOPK_INSTALL)
        self.assertEqual(topk.call_args.args[0], 3)
        self.assertIsNone(topk.call_args.kwargs["category_id"])
        self.assertEqual(len(items), 1)

    def test_milvus_failure_falls_back_topk(self) -> None:
        with (
            patch("recommender.online.service.load_user_seed_ids", return_value=["s1"]),
            patch("recommender.online.service.get_loaded_collection", return_value=object()),
            patch(
                "recommender.online.service.recommend_by_ids",
                side_effect=RuntimeError("milvus down"),
            ),
            patch(
                "recommender.online.service.load_topk_install_items",
                return_value=[RecommendItem("t1", 1.0)],
            ) as topk,
        ):
            items, source = recommend_for_user(
                user_id="u1",
                top_k=5,
                category_id="software-development",
            )
        self.assertEqual(source, SOURCE_TOPK_INSTALL)
        self.assertEqual(items[0].asset_id, "t1")
        self.assertEqual(topk.call_args.args[0], 5)

    def test_recommend_by_ids_uses_category_in_search(self) -> None:
        collection = MagicMock()
        with (
            patch(
                "recommender.online.service.fetch_embeddings_by_ids",
                return_value={"s1": [0.1, 0.2]},
            ),
            patch(
                "recommender.online.service.search_vectors",
                return_value=[[("r1", 0.9), ("s1", 0.8)]],
            ) as search,
        ):
            items = recommend_by_ids(
                ["s1"],
                5,
                collection=collection,
                category_id="software-development",
            )
        self.assertEqual([i.asset_id for i in items], ["r1"])
        self.assertEqual(search.call_args.kwargs["category_id"], "software-development")

    def test_recommend_by_ids_uses_plugin_type_in_search(self) -> None:
        collection = MagicMock()
        with (
            patch(
                "recommender.online.service.fetch_embeddings_by_ids",
                return_value={"s1": [0.1, 0.2]},
            ),
            patch(
                "recommender.online.service.search_vectors",
                return_value=[[("r1", 0.9), ("s1", 0.8)]],
            ) as search,
        ):
            recommend_by_ids(["s1"], 5, collection=collection, plugin_type="skill")
        self.assertEqual(search.call_args.kwargs["plugin_type"], "skill")


class TestMmrNoAssert(unittest.TestCase):
    def test_mmr_basic(self) -> None:
        items = [
            RecommendItem("a", 1.0),
            RecommendItem("b", 0.9),
            RecommendItem("c", 0.8),
        ]
        emb = {
            "a": [1.0, 0.0],
            "b": [0.9, 0.1],
            "c": [0.0, 1.0],
        }
        out = mmr_rerank(items, emb, top_k=2)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(isinstance(x, RecommendItem) for x in out))


class TestUpsertBatchPayload(unittest.TestCase):
    def test_upsert_includes_category_column(self) -> None:
        import numpy as np
        from recommender.offline.milvus_index.embedding import upsert_batch

        collection = MagicMock()
        vectors = np.asarray([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)
        n = upsert_batch(
            collection,
            ["a", "b"],
            vectors,
            category_ids=["software-development", ""],
            plugin_types=["skill", "swarmskill"],
        )
        self.assertEqual(n, 2)
        payload = collection.upsert.call_args.args[0]
        self.assertEqual(payload[0], ["a", "b"])
        self.assertEqual(payload[1], ["software-development", ""])
        self.assertEqual(payload[2], ["skill", "swarmskill"])
        self.assertEqual(len(payload[3]), 2)

    def test_upsert_plugin_type_length_mismatch(self) -> None:
        import numpy as np
        from recommender.offline.milvus_index.embedding import upsert_batch

        collection = MagicMock()
        vectors = np.asarray([[0.1, 0.2]], dtype=np.float32)
        with self.assertRaises(ValueError):
            upsert_batch(collection, ["a"], vectors, plugin_types=["x", "y"])

    def test_upsert_category_length_mismatch(self) -> None:
        import numpy as np
        from recommender.offline.milvus_index.embedding import upsert_batch

        collection = MagicMock()
        vectors = np.asarray([[0.1, 0.2]], dtype=np.float32)
        with self.assertRaises(ValueError):
            upsert_batch(collection, ["a"], vectors, category_ids=["x", "y"])


class TestEnsureCollectionSchema(unittest.TestCase):
    def test_existing_missing_category_raises(self) -> None:
        from recommender.offline.milvus_index.milvus_client import (
            CollectionConfig,
            ensure_collection,
        )

        cfg = CollectionConfig("h", 19530, "skill_index", 4, recreate=False)
        fake_collection = MagicMock()
        fake_collection.schema.fields = [
            SimpleNamespace(name="asset_id"),
            SimpleNamespace(name="embedding"),
        ]
        with (
            patch("pymilvus.utility.has_collection", return_value=True),
            patch("pymilvus.Collection", return_value=fake_collection),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                ensure_collection(cfg)
        self.assertIn("category_id", str(ctx.exception))

    def test_existing_missing_plugin_type_raises(self) -> None:
        from recommender.offline.milvus_index.milvus_client import (
            CollectionConfig,
            ensure_collection,
        )

        cfg = CollectionConfig("h", 19530, "skill_index", 4, recreate=False)
        fake_collection = MagicMock()
        fake_collection.schema.fields = [
            SimpleNamespace(name="asset_id"),
            SimpleNamespace(name="category_id"),
            SimpleNamespace(name="embedding"),
        ]
        with (
            patch("pymilvus.utility.has_collection", return_value=True),
            patch("pymilvus.Collection", return_value=fake_collection),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                ensure_collection(cfg)
        self.assertIn("plugin_type", str(ctx.exception))


class TestPromoteCollectionAlias(unittest.TestCase):
    def test_first_install_creates_alias(self) -> None:
        from recommender.offline.milvus_index.milvus_client import promote_collection_alias

        with (
            patch("pymilvus.utility.has_collection", return_value=True),
            patch(
                "recommender.offline.milvus_index.milvus_client.resolve_physical_name",
                return_value=None,
            ),
            patch("pymilvus.utility.create_alias") as create_alias,
            patch("pymilvus.utility.alter_alias") as alter_alias,
            patch("pymilvus.utility.rename_collection") as rename,
        ):
            previous = promote_collection_alias("skill_index", "skill_index__new")
        self.assertIsNone(previous)
        create_alias.assert_called_once_with(
            collection_name="skill_index__new",
            alias="skill_index",
        )
        alter_alias.assert_not_called()
        rename.assert_not_called()

    def test_existing_physical_renames_then_aliases(self) -> None:
        from recommender.offline.milvus_index.milvus_client import promote_collection_alias

        with (
            patch("pymilvus.utility.has_collection", return_value=True),
            patch(
                "recommender.offline.milvus_index.milvus_client.resolve_physical_name",
                return_value="skill_index",
            ),
            patch("pymilvus.utility.rename_collection") as rename,
            patch("pymilvus.utility.create_alias") as create_alias,
            patch("pymilvus.utility.alter_alias") as alter_alias,
        ):
            previous = promote_collection_alias("skill_index", "skill_index__new")
        self.assertTrue(str(previous).startswith("skill_index__old_"))
        rename.assert_called_once()
        self.assertEqual(rename.call_args.args[0], "skill_index")
        self.assertEqual(rename.call_args.args[1], previous)
        create_alias.assert_called_once_with(
            collection_name="skill_index__new",
            alias="skill_index",
        )
        alter_alias.assert_not_called()

    def test_create_alias_failure_renames_parked_back(self) -> None:
        from recommender.offline.milvus_index.milvus_client import promote_collection_alias

        with (
            patch("pymilvus.utility.has_collection", return_value=True),
            patch(
                "recommender.offline.milvus_index.milvus_client.resolve_physical_name",
                return_value="skill_index",
            ),
            patch("pymilvus.utility.rename_collection") as rename,
            patch(
                "pymilvus.utility.create_alias",
                side_effect=RuntimeError("create_alias failed"),
            ),
            patch("pymilvus.utility.alter_alias") as alter_alias,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                promote_collection_alias("skill_index", "skill_index__new")
        self.assertIn("create_alias failed", str(ctx.exception))
        self.assertEqual(rename.call_count, 2)
        parked = rename.call_args_list[0].args[1]
        self.assertEqual(rename.call_args_list[0].args, ("skill_index", parked))
        self.assertEqual(rename.call_args_list[1].args, (parked, "skill_index"))
        alter_alias.assert_not_called()

    def test_create_alias_and_rollback_failure_raises_stuck(self) -> None:
        from recommender.offline.milvus_index.milvus_client import (
            AliasCutoverStuckError,
            promote_collection_alias,
        )

        def _rename(src: str, dst: str) -> None:
            if src == "skill_index":
                return None
            raise RuntimeError("rollback rename failed")

        with (
            patch("pymilvus.utility.has_collection", return_value=True),
            patch(
                "recommender.offline.milvus_index.milvus_client.resolve_physical_name",
                return_value="skill_index",
            ),
            patch("pymilvus.utility.rename_collection", side_effect=_rename),
            patch(
                "pymilvus.utility.create_alias",
                side_effect=RuntimeError("create_alias failed"),
            ),
            patch("pymilvus.utility.alter_alias") as alter_alias,
        ):
            with self.assertRaises(AliasCutoverStuckError) as ctx:
                promote_collection_alias("skill_index", "skill_index__new")
        self.assertIn("skill_index", str(ctx.exception))
        self.assertIn("new_physical='skill_index__new'", str(ctx.exception))
        alter_alias.assert_not_called()

    def test_existing_alias_is_altered(self) -> None:
        from recommender.offline.milvus_index.milvus_client import promote_collection_alias

        with (
            patch("pymilvus.utility.has_collection", return_value=True),
            patch(
                "recommender.offline.milvus_index.milvus_client.resolve_physical_name",
                return_value="skill_index__old",
            ),
            patch("pymilvus.utility.alter_alias") as alter_alias,
            patch("pymilvus.utility.create_alias") as create_alias,
            patch("pymilvus.utility.rename_collection") as rename,
        ):
            previous = promote_collection_alias("skill_index", "skill_index__new")
        self.assertEqual(previous, "skill_index__old")
        alter_alias.assert_called_once_with(
            collection_name="skill_index__new",
            alias="skill_index",
        )
        create_alias.assert_not_called()
        rename.assert_not_called()


class TestListRecommendGate(unittest.TestCase):
    """Mirror list_plugins recommend gate conditions (pure logic)."""

    @staticmethod
    def use_recommend(*, order_by: str, keyword: str, enabled: bool, category_id: str = "") -> bool:
        use = (
            (order_by or "").strip() == "recommend"
            and not (keyword or "").strip()
            and not (category_id or "").strip()
        )
        if use and not enabled:
            return False
        return use

    def test_featured_tab_allowed(self) -> None:
        self.assertTrue(self.use_recommend(order_by="recommend", keyword="", enabled=True))

    def test_category_tab_uses_install_count(self) -> None:
        self.assertFalse(
            self.use_recommend(
                order_by="recommend",
                keyword="",
                enabled=True,
                category_id="software-development",
            )
        )

    def test_keyword_disables(self) -> None:
        self.assertFalse(self.use_recommend(order_by="recommend", keyword="foo", enabled=True))

    def test_disabled_flag(self) -> None:
        self.assertFalse(self.use_recommend(order_by="recommend", keyword="", enabled=False))

    def test_other_order(self) -> None:
        self.assertFalse(self.use_recommend(order_by="install_count", keyword="", enabled=True))

    @staticmethod
    def featured_search_allowlist(
        *,
        order_by: str,
        keyword: str,
        enabled: bool,
        category_id: str = "",
    ) -> bool:
        return (
            (order_by or "").strip() == "recommend"
            and bool((keyword or "").strip())
            and not (category_id or "").strip()
            and enabled
        )

    def test_featured_search_uses_allowlist(self) -> None:
        self.assertTrue(
            self.featured_search_allowlist(order_by="recommend", keyword="foo", enabled=True)
        )

    def test_featured_search_allowlist_skips_category(self) -> None:
        self.assertFalse(
            self.featured_search_allowlist(
                order_by="recommend",
                keyword="foo",
                enabled=True,
                category_id="software-development",
            )
        )

    def test_featured_search_allowlist_requires_enabled(self) -> None:
        self.assertFalse(
            self.featured_search_allowlist(order_by="recommend", keyword="foo", enabled=False)
        )

    def test_all_tab_search_no_allowlist(self) -> None:
        self.assertFalse(
            self.featured_search_allowlist(order_by="install_count", keyword="foo", enabled=True)
        )

    def test_intersect_keeps_retrieval_order(self) -> None:
        hits = ["a", "b", "c", "d"]
        allow = {"d", "b", "x"}
        self.assertEqual([iid for iid in hits if iid in allow], ["b", "d"])


if __name__ == "__main__":
    unittest.main()

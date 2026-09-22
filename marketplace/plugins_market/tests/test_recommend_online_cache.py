# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://test:test@127.0.0.1:3306/test")

from plugins_market.core.viewer_context import ANONYMOUS_VIEWER, ViewerContext
from plugins_market.recommender import anon_card_cache
from plugins_market.recommender.anon_card_cache import plaza_cache_top_k
from plugins_market.recommender.schemas import RecommendRequest
from plugins_market.routers.recommender import (
    _anon_recommend_cacheable,
    _plugin_type_on_market,
    recommend,
)
from plugins_market.schemas.plugin import PluginListItem
from recommender.online import redis_seeds
from recommender.online.types import RecommendItem


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        host="h",
        port=6379,
        db=0,
        ssl=False,
        password="",
        topk_install=SimpleNamespace(key="k"),
        user_seq=SimpleNamespace(key_prefix="p"),
    )


SWARM_HOME_TOP_K = 6


def _plaza_key():
    return anon_card_cache.cache_key("skill", "")


def _card_dump(asset_id: str) -> dict:
    return PluginListItem(
        asset_id=asset_id,
        asset_type="plugin",
        name=asset_id,
        publisher_id="p",
        publisher_name="p",
        plugin_type="skill",
    ).model_dump()


class AnonCardCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        anon_card_cache.reset()

    def tearDown(self) -> None:
        anon_card_cache.reset()

    def test_miss_then_hit(self) -> None:
        key = _plaza_key()
        self.assertIsNone(anon_card_cache.get(key))
        self.assertTrue(anon_card_cache.put(key, "topk_install", [{"asset_id": "a1"}]))
        hit = anon_card_cache.get(key)
        self.assertIsNotNone(hit)
        source, items = hit
        self.assertEqual(source, "topk_install")
        self.assertEqual(items[0]["asset_id"], "a1")

    def test_take_slices_prefix_for_smaller_top_k(self) -> None:
        key = _plaza_key()
        anon_card_cache.put(
            key,
            "topk_install",
            [{"asset_id": "a1"}, {"asset_id": "a2"}, {"asset_id": "a3"}],
        )
        taken = anon_card_cache.take(key, 2)
        self.assertIsNotNone(taken)
        source, items = taken
        self.assertEqual(source, "topk_install")
        self.assertEqual([row["asset_id"] for row in items], ["a1", "a2"])
        self.assertEqual(len(anon_card_cache.get(key)[1]), 3)

    def test_take_skips_when_request_exceeds_plaza_page(self) -> None:
        key = _plaza_key()
        anon_card_cache.put(key, "topk_install", [{"asset_id": "a1"}])
        self.assertIsNone(anon_card_cache.take(key, plaza_cache_top_k() + 1))

    def test_put_skips_empty(self) -> None:
        key = _plaza_key()
        self.assertFalse(anon_card_cache.put(key, "topk_install", []))
        self.assertIsNone(anon_card_cache.get(key))

    def test_evicts_oldest_not_entire_store(self) -> None:
        with patch("plugins_market.recommender.anon_card_cache._MAX_KEYS", 2):
            anon_card_cache.put(("skill", ""), "topk_install", [{"asset_id": "a"}])
            anon_card_cache.put(("swarmskill", ""), "topk_install", [{"asset_id": "b"}])
            anon_card_cache.put(("skill", "cat"), "topk_install", [{"asset_id": "c"}])
            self.assertIsNone(anon_card_cache.get(("skill", "")))
            self.assertIsNotNone(anon_card_cache.get(("swarmskill", "")))
            self.assertIsNotNone(anon_card_cache.get(("skill", "cat")))

    def test_expiry(self) -> None:
        key = _plaza_key()
        anon_card_cache.put(key, "topk_install", [{"asset_id": "a1"}])
        with patch("plugins_market.recommender.anon_card_cache.time.monotonic", return_value=10**9):
            self.assertIsNone(anon_card_cache.get(key))


class RedisClientReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        redis_seeds.reset_redis_clients()

    def tearDown(self) -> None:
        redis_seeds.reset_redis_clients()

    def test_same_config_reuses_client_and_pings_once(self) -> None:
        client = MagicMock()
        cfg = _cfg()
        with patch("recommender.online.redis_seeds.create_redis_client", return_value=client) as create:
            with patch("recommender.online.redis_seeds.load_config", return_value=SimpleNamespace(redis=cfg)):
                first = redis_seeds.get_redis_client()
                second = redis_seeds.get_redis_client()
        self.assertIs(first[0], client)
        self.assertIs(second[0], client)
        self.assertEqual(create.call_count, 1)
        client.ping.assert_called_once()

    def test_get_failure_reconnects_once(self) -> None:
        dead = MagicMock()
        dead.get.side_effect = ConnectionError("gone")
        live = MagicMock()
        live.get.return_value = json.dumps({"items": [{"asset_id": "a1", "rank": 1, "plugin_type": "skill"}]})
        cfg = _cfg()
        with patch("recommender.online.redis_seeds.create_redis_client", side_effect=[dead, live]) as create:
            with patch("recommender.online.redis_seeds.load_config", return_value=SimpleNamespace(redis=cfg)):
                items = redis_seeds.load_topk_install_items(1, plugin_type="skill")
        self.assertEqual([x.asset_id for x in items], ["a1"])
        self.assertEqual(create.call_count, 2)
        dead.close.assert_called()


class PluginTypeOnMarketTests(unittest.TestCase):
    def test_empty_and_hub_types_are_on_market(self) -> None:
        self.assertTrue(_plugin_type_on_market(""))
        self.assertTrue(_plugin_type_on_market("skill"))
        self.assertTrue(_plugin_type_on_market("swarmskill"))
        self.assertTrue(_plugin_type_on_market("skill,skillpack"))

    def test_skillpack_alone_is_not_on_market(self) -> None:
        self.assertFalse(_plugin_type_on_market("skillpack"))


class AnonRecommendCacheableTests(unittest.TestCase):
    def test_cold_start_includes_system_token(self) -> None:
        self.assertTrue(_anon_recommend_cacheable(ANONYMOUS_VIEWER, ""))
        admin = ViewerContext(user_id="system_admin", user_login="system_admin", is_system_admin=True)
        self.assertTrue(_anon_recommend_cacheable(admin, ""))
        bearer = ViewerContext(user_id="u1", user_login="alice", is_system_admin=False)
        self.assertFalse(_anon_recommend_cacheable(bearer, "u1"))
        self.assertFalse(_anon_recommend_cacheable(admin, "u1"))


class SingleflightTests(unittest.TestCase):
    def setUp(self) -> None:
        anon_card_cache.reset()

    def tearDown(self) -> None:
        anon_card_cache.reset()

    def test_concurrent_waiters_share_one_fn(self) -> None:
        import threading

        key = _plaza_key()
        n = {"c": 0}
        entered = threading.Event()
        release = threading.Event()

        def fn() -> str:
            n["c"] += 1
            entered.set()
            self.assertTrue(release.wait(2))
            return "ok"

        results: list[str] = []

        def run() -> None:
            results.append(anon_card_cache.singleflight(key, fn))

        t1 = threading.Thread(target=run)
        t1.start()
        self.assertTrue(entered.wait(1))
        t2 = threading.Thread(target=run)
        t2.start()
        time.sleep(0.05)
        release.set()
        t1.join(2)
        t2.join(2)
        self.assertEqual(n["c"], 1)
        self.assertEqual(sorted(results), ["ok", "ok"])


class RecommendCacheGateTests(unittest.TestCase):
    def setUp(self) -> None:
        anon_card_cache.reset()

    def tearDown(self) -> None:
        anon_card_cache.reset()

    def _admin(self) -> ViewerContext:
        return ViewerContext(user_id="system_admin", user_login="system_admin", is_system_admin=True)

    def test_system_admin_empty_user_reads_public_cache(self) -> None:
        key = _plaza_key()
        cached_row = {**_card_dump("cached"), "score": 1.0}
        anon_card_cache.put(key, "topk_install", [cached_row])
        with patch("plugins_market.routers.recommender._ensure_enabled"):
            with patch("plugins_market.routers.recommender.run_recommend_for_user") as recall:
                resp = recommend(
                    RecommendRequest(plugin_type="skill", top_k=SWARM_HOME_TOP_K),
                    viewer=self._admin(),
                    storage=MagicMock(),
                )
        self.assertEqual(resp.data.items[0].asset_id, "cached")
        recall.assert_not_called()

    def test_smaller_top_k_slices_cached_plaza_page(self) -> None:
        key = _plaza_key()
        rows = [{**_card_dump(f"a{i}"), "score": float(i)} for i in range(1, 4)]
        anon_card_cache.put(key, "topk_install", rows)
        with patch("plugins_market.routers.recommender._ensure_enabled"):
            with patch("plugins_market.routers.recommender.run_recommend_for_user") as recall:
                resp = recommend(
                    RecommendRequest(plugin_type="skill", top_k=2),
                    viewer=self._admin(),
                    storage=MagicMock(),
                )
        self.assertEqual([it.asset_id for it in resp.data.items], ["a1", "a2"])
        recall.assert_not_called()

    def test_system_admin_empty_user_hydrates_anonymous_and_writes_cache(self) -> None:
        live = MagicMock()
        live.asset_id = "live"
        live.model_dump.return_value = _card_dump("live")
        with patch("plugins_market.routers.recommender._ensure_enabled"):
            with patch(
                "plugins_market.routers.recommender.run_recommend_for_user",
                return_value=([RecommendItem(asset_id="live", score=0.9)], "topk_install"),
            ):
                with patch(
                    "plugins_market.routers.recommender.filter_recommend_ranked_ids",
                    return_value=["live"],
                ) as filt:
                    with patch(
                        "plugins_market.routers.recommender.hydrate_plugin_list_items",
                        return_value=[live],
                    ) as hyd:
                        with patch("plugins_market.routers.recommender.SessionLocal") as session_local:
                            session_local.return_value = MagicMock()
                            resp = recommend(
                                RecommendRequest(plugin_type="skill", top_k=SWARM_HOME_TOP_K),
                                viewer=self._admin(),
                                storage=MagicMock(),
                            )
        self.assertEqual(resp.data.items[0].asset_id, "live")
        self.assertEqual(filt.call_args.kwargs["viewer"], ANONYMOUS_VIEWER)
        self.assertEqual(hyd.call_args.kwargs["viewer"], ANONYMOUS_VIEWER)
        stored = anon_card_cache.get(_plaza_key())
        self.assertIsNotNone(stored)
        source, items = stored
        self.assertEqual(source, "topk_install")
        self.assertEqual(items[0]["asset_id"], "live")

    def test_system_admin_personalized_skips_cache(self) -> None:
        key = _plaza_key()
        cached_row = {**_card_dump("cached"), "score": 1.0}
        anon_card_cache.put(key, "topk_install", [cached_row])
        live = MagicMock()
        live.asset_id = "live"
        live.model_dump.return_value = _card_dump("live")
        admin = self._admin()
        with patch("plugins_market.routers.recommender._ensure_enabled"):
            with patch(
                "plugins_market.routers.recommender.run_recommend_for_user",
                return_value=([RecommendItem(asset_id="live", score=0.9)], "user_history"),
            ):
                with patch(
                    "plugins_market.routers.recommender.filter_recommend_ranked_ids",
                    return_value=["live"],
                ) as filt:
                    with patch(
                        "plugins_market.routers.recommender.hydrate_plugin_list_items",
                        return_value=[live],
                    ) as hyd:
                        with patch("plugins_market.routers.recommender.SessionLocal") as session_local:
                            session_local.return_value = MagicMock()
                            resp = recommend(
                                RecommendRequest(plugin_type="skill", top_k=SWARM_HOME_TOP_K, user_id="u1"),
                                viewer=admin,
                                storage=MagicMock(),
                            )
        self.assertEqual(resp.data.items[0].asset_id, "live")
        self.assertEqual(filt.call_args.kwargs["viewer"], admin)
        self.assertEqual(hyd.call_args.kwargs["viewer"], admin)
        source, items = anon_card_cache.get(key)
        self.assertEqual(items[0]["asset_id"], "cached")
        self.assertEqual(source, "topk_install")

    def test_anonymous_empty_hydrate_does_not_write_cache(self) -> None:
        with patch("plugins_market.routers.recommender._ensure_enabled"):
            with patch(
                "plugins_market.routers.recommender.run_recommend_for_user",
                return_value=([], "topk_install"),
            ):
                with patch("plugins_market.routers.recommender.filter_recommend_ranked_ids", return_value=[]):
                    with patch("plugins_market.routers.recommender.hydrate_plugin_list_items", return_value=[]):
                        with patch("plugins_market.routers.recommender.SessionLocal") as session_local:
                            session_local.return_value = MagicMock()
                            recommend(
                                RecommendRequest(plugin_type="skill", top_k=SWARM_HOME_TOP_K),
                                viewer=ANONYMOUS_VIEWER,
                                storage=MagicMock(),
                            )
        self.assertIsNone(anon_card_cache.get(_plaza_key()))

    def test_concurrent_plaza_miss_hydrates_once(self) -> None:
        import threading

        live = MagicMock()
        live.asset_id = "live"
        live.model_dump.return_value = _card_dump("live")
        entered = threading.Event()
        release = threading.Event()

        def _hydrate(*_a, **_k):
            entered.set()
            self.assertTrue(release.wait(2))
            return [live]

        with patch("plugins_market.routers.recommender._ensure_enabled"):
            with patch(
                "plugins_market.routers.recommender.run_recommend_for_user",
                return_value=([RecommendItem(asset_id="live", score=0.9)], "topk_install"),
            ):
                with patch(
                    "plugins_market.routers.recommender.filter_recommend_ranked_ids",
                    return_value=["live"],
                ):
                    with patch(
                        "plugins_market.routers.recommender.hydrate_plugin_list_items",
                        side_effect=_hydrate,
                    ) as hyd:
                        with patch("plugins_market.routers.recommender.SessionLocal") as session_local:
                            session_local.return_value = MagicMock()
                            results: list[str] = []

                            def run() -> None:
                                resp = recommend(
                                    RecommendRequest(plugin_type="skill", top_k=SWARM_HOME_TOP_K),
                                    viewer=self._admin(),
                                    storage=MagicMock(),
                                )
                                results.append(resp.data.items[0].asset_id)

                            t1 = threading.Thread(target=run)
                            t1.start()
                            self.assertTrue(entered.wait(1))
                            t2 = threading.Thread(target=run)
                            t2.start()
                            time.sleep(0.05)
                            release.set()
                            t1.join(2)
                            t2.join(2)
                            self.assertEqual(hyd.call_count, 1)
                            self.assertEqual(sorted(results), ["live", "live"])


if __name__ == "__main__":
    unittest.main()

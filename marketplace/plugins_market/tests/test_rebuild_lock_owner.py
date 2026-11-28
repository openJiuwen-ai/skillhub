# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("STORE_DB_URL", "mysql+pymysql://test:test@127.0.0.1:3306/test")

from plugins_market.retrieval import daily_rebuild


class FakeRedis:
    """Minimal redis stub; eval mimics the owner-compare-and-delete Lua script."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.eval_calls: list[tuple] = []
        self.delete_calls: list[str] = []
        self.last_set: tuple[str, str, bool, int | None] | None = None

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        self.last_set = (key, value, nx, ex)
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        self.eval_calls.append((script, keys_and_args))
        key, owner = keys_and_args[0], keys_and_args[1]
        if self.data.get(key) == owner:
            self.data.pop(key, None)
            return 1
        return 0

    def delete(self, *keys: str) -> int:
        self.delete_calls.extend(keys)
        removed = 0
        for key in keys:
            if self.data.pop(key, None) is not None:
                removed += 1
        return removed


def _storage() -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(bucket_name="bucket", storage_type="OBS"))


class RebuildLockOwnerTests(unittest.TestCase):
    def test_acquire_stores_owner_token(self) -> None:
        fake = FakeRedis()
        owner = daily_rebuild._acquire_lock_with_owner(fake, daily_rebuild._REBUILD_LOCK_KEY)
        self.assertIsNotNone(owner)
        self.assertRegex(owner, r"^[0-9a-f]{32}$")
        self.assertEqual(fake.data[daily_rebuild._REBUILD_LOCK_KEY], owner)
        self.assertEqual(
            fake.last_set,
            (daily_rebuild._REBUILD_LOCK_KEY, owner, True, daily_rebuild._REBUILD_LOCK_TTL_SECONDS),
        )

    def test_release_only_when_owner_matches(self) -> None:
        fake = FakeRedis()
        owner = daily_rebuild._acquire_lock_with_owner(fake, daily_rebuild._REBUILD_LOCK_KEY)
        daily_rebuild._release_lock_if_owner(fake, daily_rebuild._REBUILD_LOCK_KEY, owner)
        self.assertNotIn(daily_rebuild._REBUILD_LOCK_KEY, fake.data)

        daily_rebuild._acquire_lock_with_owner(fake, daily_rebuild._REBUILD_LOCK_KEY)
        daily_rebuild._release_lock_if_owner(fake, daily_rebuild._REBUILD_LOCK_KEY, "stale-owner")
        self.assertIn(daily_rebuild._REBUILD_LOCK_KEY, fake.data)

    def test_release_swallows_eval_failure(self) -> None:
        fake = FakeRedis()
        owner = daily_rebuild._acquire_lock_with_owner(fake, daily_rebuild._REBUILD_LOCK_KEY)
        with patch.object(fake, "eval", side_effect=RuntimeError("boom")):
            daily_rebuild._release_lock_if_owner(fake, daily_rebuild._REBUILD_LOCK_KEY, owner)
        self.assertIn(daily_rebuild._REBUILD_LOCK_KEY, fake.data)

    def test_rebuild_all_releases_via_owner_token(self) -> None:
        fake = FakeRedis()
        with patch.object(daily_rebuild, "rebuild_one_group") as rebuild_one_group:
            daily_rebuild.rebuild_all(
                db_factory=MagicMock(),
                skill_prefix="skills-index",
                plugin_prefix="plugins-index",
                storage=_storage(),
                redis_client=fake,
            )
        rebuild_one_group.assert_called()
        stored = fake.data.get(daily_rebuild._REBUILD_LOCK_KEY)
        self.assertIsNone(stored)
        self.assertEqual(len(fake.eval_calls), 1)
        self.assertEqual(fake.delete_calls, [])

    def test_rebuild_all_skips_when_lock_held_by_other_owner(self) -> None:
        fake = FakeRedis()
        fake.data[daily_rebuild._REBUILD_LOCK_KEY] = "other-owner"
        with patch.object(daily_rebuild, "rebuild_one_group") as rebuild_one_group:
            daily_rebuild.rebuild_all(
                db_factory=MagicMock(),
                skill_prefix="skills-index",
                plugin_prefix="plugins-index",
                storage=_storage(),
                redis_client=fake,
            )
        rebuild_one_group.assert_not_called()
        self.assertEqual(fake.data[daily_rebuild._REBUILD_LOCK_KEY], "other-owner")
        self.assertEqual(fake.eval_calls, [])


if __name__ == "__main__":
    unittest.main()

# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_MARKETPLACE = Path(__file__).resolve().parents[2]
for _path in (_MARKETPLACE, _MARKETPLACE / "retrieval"):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.append(_text)

from indexing.embedding.index import (
    EmbeddingInputTooLong,
    EmbeddingRecord,
    OFFLINE_EMBED_MIN_INTERVAL_S,
    OpenAIEmbeddingClient,
    _embed_records_skipping_overlong,
    configure_offline_embedding_redis,
)
from indexing.embedding import index as embedding_index
from recommender.offline.milvus_index import embedding as rec_embedding
from recommender.offline.milvus_index import lock as milvus_lock
from recommender.offline.milvus_index.pipeline import _index_skills
from recommender.offline.milvus_index.state import (
    IndexedAsset,
    IndexState,
    load_state_shared,
    merge_indexed_assets,
    save_state,
)
from recommender.offline.package_sync.db import ActiveSkillVersion


def _vectors(texts: list[str]):
    return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0]) for _ in texts])


def _client(create, *, min_interval_s: float = 0.0) -> OpenAIEmbeddingClient:
    embeddings = SimpleNamespace(create=create)
    return OpenAIEmbeddingClient(
        client=SimpleNamespace(embeddings=embeddings),
        model="bge-m3",
        min_interval_s=min_interval_s,
    )


def _skill(asset_id: str) -> ActiveSkillVersion:
    return ActiveSkillVersion(
        asset_id=asset_id,
        name=asset_id,
        display_name=asset_id,
        short_desc="desc",
        plugin_type="skill",
        status="online",
        latest_version="1.0.0",
        version_id="v",
        file_path="publisher/asset",
        artifact_sha256="sha",
    )


def _asset(asset_id: str) -> IndexedAsset:
    return IndexedAsset(
        version="1.0.0",
        artifact_sha256="sha",
        indexed_at="2026-01-01T00:00:00+00:00",
        category_id="",
        plugin_type="skill",
    )


class _Collection:
    @staticmethod
    def upsert(payload) -> None:
        return None


class _Model:
    def __init__(self, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def embed_texts(self, texts, batch_size: int = 16):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("Error code: 429 - the rate limit is 100 per minute.")
        return [[1.0, 0.0] for _ in texts]


def test_embedding_retries_per_minute_limit(monkeypatch) -> None:
    now = {"t": 1000.0}
    slept: list[float] = []

    def monotonic() -> float:
        return now["t"]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now["t"] += seconds

    monkeypatch.setattr("indexing.embedding.index.time.monotonic", monotonic)
    monkeypatch.setattr("indexing.embedding.index.time.sleep", sleep)
    calls = {"n": 0}

    def create(model, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Too many requests, the rate limit is 100 per minute.")
        return _vectors(list(kwargs["input"]))

    vectors = _client(create, min_interval_s=0.8).embed_texts(["塔罗牌"], batch_size=1)
    assert len(vectors) == 1
    assert slept == [61.0]


def test_online_embedding_does_not_sleep_on_rate_limit() -> None:
    def create(model, **kwargs):
        raise RuntimeError("Too many requests, the rate limit is 100 per minute.")

    with pytest.raises(RuntimeError, match="per minute"):
        _client(create, min_interval_s=0).embed_texts(["塔罗牌"], batch_size=1)


def test_embedding_splits_context_length_batch() -> None:
    seen: list[int] = []

    def create(model, **kwargs):
        batch = list(kwargs["input"])
        seen.append(len(batch))
        if len(batch) > 1:
            raise RuntimeError("This model's maximum context length is 8192 tokens. you requested 10289")
        return _vectors(batch)

    vectors = _client(create).embed_texts(["aa", "bb"], batch_size=8)
    assert len(vectors) == 2
    assert seen.count(1) == 2


def test_embedding_rejects_single_overlong_text() -> None:
    def create(model, **kwargs):
        text = list(kwargs["input"])[0]
        if len(text) > 2:
            raise RuntimeError("maximum context length is 8192 tokens")
        return _vectors([text])

    with pytest.raises(EmbeddingInputTooLong):
        _client(create).embed_texts(["abcdefgh"], batch_size=1)


def test_embedding_paces_offline_batches(monkeypatch) -> None:
    now = {"t": 100.0}
    slept: list[float] = []

    def monotonic() -> float:
        return now["t"]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now["t"] += seconds

    monkeypatch.setattr("indexing.embedding.index.time.monotonic", monotonic)
    monkeypatch.setattr("indexing.embedding.index.time.sleep", sleep)

    def create(model, **kwargs):
        return _vectors(list(kwargs["input"]))

    _client(create, min_interval_s=0.8).embed_texts(["a", "b"], batch_size=1)
    assert slept == [pytest.approx(0.8)]


def test_merge_keeps_prior_assets_and_drops_package_failures() -> None:
    previous = IndexState(
        updated_at="2026-01-01T00:00:00+00:00",
        assets={"old": _asset("old"), "bad": _asset("bad"), "done": _asset("done")},
    )
    fresh = _asset("new")
    merged = merge_indexed_assets(
        ["old", "bad", "done", "new"],
        previous,
        {"new": fresh},
        ["bad"],
    )
    assert set(merged) == {"old", "done", "new"}
    assert merged["new"] is fresh


def test_shared_state_uses_redis_when_local_file_is_empty(tmp_path: Path) -> None:
    remote = {
        "updated_at": "2026-10-27T00:00:00+00:00",
        "assets": {
            "deckaura": {
                "version": "1.0.4",
                "artifact_sha256": "abc",
                "indexed_at": "2026-10-27T00:00:00+00:00",
                "category_id": "",
                "plugin_type": "skill",
            }
        },
    }

    class Redis:
        @staticmethod
        def get(key):
            return json.dumps(remote)

    loaded = load_state_shared(tmp_path / "missing.json", Redis())
    assert set(loaded.assets) == {"deckaura"}


def test_shared_state_keeps_newer_local_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(
        IndexState(
            updated_at="2026-10-27T02:00:00+00:00",
            assets={"local": _asset("local")},
        ),
        path,
    )
    remote = {
        "updated_at": "2026-10-27T01:00:00+00:00",
        "assets": {
            "remote": {
                "version": "1",
                "artifact_sha256": None,
                "indexed_at": "2026-10-27T01:00:00+00:00",
                "category_id": "",
                "plugin_type": "skill",
            }
        },
    }

    class Redis:
        @staticmethod
        def get(key):
            return json.dumps(remote)

    loaded = load_state_shared(path, Redis())
    assert set(loaded.assets) == {"local"}


def test_index_checkpoint_survives_later_rate_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "recommender.offline.milvus_index.pipeline.resolve_local_zip",
        lambda download_dir, skill: tmp_path / "ok.zip",
    )
    monkeypatch.setattr(
        "recommender.offline.milvus_index.pipeline._resolve_embedding_text",
        lambda skill, zip_path: (SimpleNamespace(embedding_text="text"), "market_fields"),
    )
    commits: list[set[str]] = []

    def on_commit(indexed, failed_ids):
        commits.append(set(indexed))

    with pytest.raises(RuntimeError, match="429"):
        _index_skills(
            _Collection(),
            _Model(fail_after=1),
            [_skill("first"), _skill("second")],
            tmp_path,
            batch_size=1,
            download_force=False,
            app_cfg=SimpleNamespace(download_dir=tmp_path),
            on_commit=on_commit,
        )
    assert commits == [{"first"}]


def test_package_failure_does_not_abort_the_rest(tmp_path: Path, monkeypatch) -> None:
    def resolve(download_dir, skill):
        if skill.asset_id == "bad":
            raise FileNotFoundError("missing zip")
        return tmp_path / "ok.zip"

    monkeypatch.setattr("recommender.offline.milvus_index.pipeline.resolve_local_zip", resolve)
    monkeypatch.setattr(
        "recommender.offline.milvus_index.pipeline._resolve_embedding_text",
        lambda skill, zip_path: (SimpleNamespace(embedding_text="text"), "market_fields"),
    )
    upserted, failed, indexed, _items, failed_ids = _index_skills(
        _Collection(),
        _Model(),
        [_skill("bad"), _skill("good")],
        tmp_path,
        batch_size=1,
        download_force=False,
        app_cfg=SimpleNamespace(download_dir=tmp_path),
    )
    assert upserted == 1
    assert failed == 1
    assert failed_ids == ["bad"]
    assert set(indexed) == {"good"}


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def eval(self, script, numkeys, key, token):
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


def test_milvus_lock_allows_one_holder(monkeypatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(milvus_lock, "create_redis_client", lambda cfg: redis)
    app = SimpleNamespace(redis=object())
    with milvus_lock.milvus_index_lock(app) as first:
        assert first.proceed
        with milvus_lock.milvus_index_lock(app) as second:
            assert not second.proceed
    with milvus_lock.milvus_index_lock(app) as third:
        assert third.proceed


def test_overlong_record_is_omitted_from_embedding_index() -> None:
    class Client:
        @staticmethod
        def embed_texts(texts, batch_size=64):
            if any(text == "LONG" for text in texts):
                raise EmbeddingInputTooLong("embedding input too long")
            return [[1.0, 0.0] for _ in texts]

    kept, vectors = _embed_records_skipping_overlong(
        [
            EmbeddingRecord(choice_id="ok", payload="ok", text="short"),
            EmbeddingRecord(choice_id="bad", payload="bad", text="LONG"),
        ],
        Client(),
        batch_size=8,
    )
    assert [record.choice_id for record in kept] == ["ok"]
    assert len(vectors) == 1


def test_index_skips_overlong_text_and_keeps_the_rest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "recommender.offline.milvus_index.pipeline.resolve_local_zip",
        lambda download_dir, skill: tmp_path / "ok.zip",
    )

    def resolve_text(skill, zip_path):
        text = "TOO_LONG" if skill.asset_id == "long" else "ok"
        return SimpleNamespace(embedding_text=text), "market_fields"

    monkeypatch.setattr(
        "recommender.offline.milvus_index.pipeline._resolve_embedding_text",
        resolve_text,
    )

    class Model:
        @staticmethod
        def embed_texts(texts, batch_size=16):
            if any(text == "TOO_LONG" for text in texts):
                raise EmbeddingInputTooLong("embedding input too long")
            return [[1.0, 0.0] for _ in texts]

    upserted, failed, indexed, _items, failed_ids = _index_skills(
        _Collection(),
        Model(),
        [_skill("long"), _skill("good")],
        tmp_path,
        batch_size=8,
        download_force=False,
        app_cfg=SimpleNamespace(download_dir=tmp_path),
    )
    assert upserted == 1
    assert failed == 0
    assert failed_ids == []
    assert set(indexed) == {"good"}


def test_online_and_offline_embedding_clients_are_separate(monkeypatch) -> None:
    created: list[float] = []
    monkeypatch.setattr(rec_embedding, "_clients", {})
    monkeypatch.setattr(
        rec_embedding,
        "_resolve_embedding_settings",
        lambda: ("http://embed", "key", "bge", 16),
    )

    def create(**kwargs):
        created.append(float(kwargs["min_interval_s"]))
        return SimpleNamespace(interval=kwargs["min_interval_s"])

    monkeypatch.setattr(rec_embedding, "_create_openai_embedding_client", create)
    online = rec_embedding.make_embedding_model()
    offline = rec_embedding.make_embedding_model(offline=True)
    assert online is not offline
    assert created == [0.0, OFFLINE_EMBED_MIN_INTERVAL_S]


def test_offline_clients_share_a_minute_budget(monkeypatch) -> None:
    state = {"n": 0}

    class SlotRedis:
        @staticmethod
        def incr(key):
            state["n"] += 1
            if state["n"] == 2:
                return 2
            return 1

        @staticmethod
        def expire(key, ttl):
            return True

        @staticmethod
        def decr(key):
            return 0

    slept: list[float] = []
    configure_offline_embedding_redis(SlotRedis())
    monkeypatch.setattr(embedding_index, "OFFLINE_EMBED_MAX_PER_MINUTE", 1)
    monkeypatch.setattr(embedding_index.time, "sleep", slept.append)
    monkeypatch.setattr(embedding_index.time, "time", lambda: 1_700_000_060.0)

    def create(model, **kwargs):
        return _vectors(list(kwargs["input"]))

    client = _client(create, min_interval_s=0.8)
    try:
        client.embed_texts(["a"], batch_size=1)
        client.embed_texts(["b"], batch_size=1)
    finally:
        configure_offline_embedding_redis(None)
    assert slept
    assert slept[0] > 0


def test_shared_state_falls_back_when_local_file_is_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{", encoding="utf-8")
    remote = {
        "updated_at": "2026-10-27T00:00:00+00:00",
        "assets": {
            "deckaura": {
                "version": "1.0.4",
                "artifact_sha256": "abc",
                "indexed_at": "2026-10-27T00:00:00+00:00",
                "category_id": "",
                "plugin_type": "skill",
            }
        },
    }

    class Redis:
        @staticmethod
        def get(key):
            return json.dumps(remote)

    loaded = load_state_shared(path, Redis())
    assert set(loaded.assets) == {"deckaura"}


def test_corrupt_state_without_redis_raises(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_state_shared(path, None)


def test_milvus_lock_error_is_not_a_skip(monkeypatch) -> None:
    def fail(cfg):
        raise ConnectionError("redis down")

    monkeypatch.setattr(milvus_lock, "create_redis_client", fail)
    with pytest.raises(milvus_lock.MilvusLockError):
        with milvus_lock.milvus_index_lock(SimpleNamespace(redis=object())):
            pass

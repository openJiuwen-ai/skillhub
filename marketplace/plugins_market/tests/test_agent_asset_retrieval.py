# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.publish_result import PUBLISH_RESULT_SUCCESS
from plugins_market.core.config import Settings
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB
from plugins_market.retrieval.daily_rebuild import (
    AgentTagRefreshOptions,
    IndexItemRecord,
    _build_tag_output_uri,
    _fetch_valid_item_records,
    _parse_agent_category_mapping_jsonl,
    _refresh_agent_categories_from_mapping,
    _run_skill_tag_refresh,
    rebuild_all,
    rebuild_one_group,
    refresh_agent_tags,
)
from plugins_market.retrieval.index_manager import _build_cid_to_asset_map
from plugins_market.retrieval.search import retrieval_search
from indexing.io import load_catalog_records, load_manifest
from indexing.models import CATALOG_FILENAME
from indexing.workflows.artifacts import BuildConfig, BuildMethod, build_catalog_records_from_nodes
from indexing.workflows.index_builder import IndexBuilder
from indexing.workflows.index_builder import _unique_dir_name
from indexing.scanners import create_scanner


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)()


def _asset(
    asset_id: str,
    *,
    asset_type: str,
    plugin_type: str,
    public_version: str | None = "1.0.0",
    visibility: str = "public",
) -> MarketAssetDB:
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type=asset_type,
        name=f"{asset_id}-name",
        display_name=f"{asset_id} display",
        short_desc=f"{asset_id} short",
        detail_desc=f"{asset_id} detail",
        publisher_id="publisher-1",
        publisher_name="publisher-1",
        tags=["shared", asset_type],
        status="PUBLISHED",
        plugin_type=plugin_type,
        publish_result=PUBLISH_RESULT_SUCCESS if public_version else "pending_moderation",
        moderation_status="APPROVED" if public_version else "PENDING",
        public_latest_version=public_version,
        latest_version="2.0.0",
        visibility=visibility,
    )


@pytest.mark.parametrize(
    ("asset_type", "storage_root"),
    [
        ("agent-plugin", "agent-plugins"),
        ("agent-template", "agent-templates"),
        ("agent-mcp", "agent-mcps"),
    ],
)
def test_agent_index_records_use_asset_type_and_public_obs_root(
    asset_type: str,
    storage_root: str,
) -> None:
    db = _db()
    db.add(_asset("wanted", asset_type=asset_type, plugin_type=asset_type))
    db.add(_asset("wrong-runtime", asset_type=asset_type, plugin_type="skill"))
    db.add(_asset("pending", asset_type=asset_type, plugin_type=asset_type, public_version=None))
    db.add(_asset("private", asset_type=asset_type, plugin_type=asset_type, visibility="private"))
    db.commit()

    records = _fetch_valid_item_records(db, asset_type, "market-bucket")

    assert [record.item_path for record in records] == [
        f"obs://market-bucket/{storage_root}/publisher-1/wanted/1.0.0/wanted-name_1.0.0.zip"
    ]
    assert records[0].metadata == {
        "asset_id": "wanted",
        "asset_type": asset_type,
        "name": "wanted-name",
        "display_name": "wanted display",
        "short_desc": "wanted short",
        "detail_desc": "wanted detail",
        "tags": ["shared", asset_type],
        "plugin_type": asset_type,
        "version": "1.0.0",
    }


def test_skill_index_record_path_remains_unchanged() -> None:
    db = _db()
    db.add(_asset("skill-one", asset_type="plugin", plugin_type="skill"))
    db.commit()

    records = _fetch_valid_item_records(db, "skill", "market-bucket")

    assert records[0].item_path == (
        "obs://market-bucket/skills/publisher-1/skill-one/1.0.0/skill-one-name_1.0.0.zip"
    )


def test_agent_scanner_uses_common_market_metadata(tmp_path) -> None:
    item_dir = tmp_path / "agent-one"
    item_dir.mkdir()
    (item_dir / "plugin.yaml").write_text(
        "name: agent-one\ndisplay_name: Package Name\ndescription: Package description\n",
        encoding="utf-8",
    )
    (tmp_path / "skills.json").write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "id": "agent-one",
                        "display_name": "Market Name",
                        "short_desc": "Market short description",
                        "detail_desc": "Market detail description",
                        "tags": ["market-tag"],
                        "asset_type": "agent-plugin",
                        "plugin_type": "agent-plugin",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    scanned = create_scanner("agent", tmp_path, display_items_dir=tmp_path).to_dict_list()

    assert scanned[0]["plugin_display_name"] == "Package Name"
    assert scanned[0]["market_display_name"] == "Market Name"
    assert scanned[0]["market_short_desc"] == "Market short description"
    assert scanned[0]["market_detail_desc"] == "Market detail description"
    assert scanned[0]["tags"] == ["market-tag"]
    assert scanned[0]["description"] == (
        "Market short description\nMarket detail description\nPackage description"
    )


def test_agent_rebuild_uses_shared_agent_scanner_and_keeps_asset_type_group() -> None:
    path = "obs://market-bucket/agent-plugins/publisher-1/asset-1/1.0.0/asset_1.0.0.zip"
    record = IndexItemRecord(path, {"asset_id": "asset-1", "asset_type": "agent-plugin"})
    storage = SimpleNamespace(config=SimpleNamespace(bucket_name="market-bucket"))
    build_config = BuildConfig()

    with (
        patch(
            "plugins_market.retrieval.daily_rebuild._fetch_valid_item_records",
            return_value=[record],
        ),
        patch("plugins_market.retrieval.daily_rebuild._gc_old_indexes"),
        patch(
            "indexing.workflows.index_builder.IndexBuilder.build",
            return_value="obs://market-bucket/agent-plugins-index/20260911010101",
        ) as build,
    ):
        rebuild_one_group(
            "agent-plugin",
            object(),
            "agent-plugins-index",
            storage,
            build_config=build_config,
            run_skill_tag=False,
        )

    assert build.call_args.kwargs["item_type"] == "agent"
    assert build.call_args.kwargs["config"].item_metadata_by_path == {
        path: {"asset_id": "asset-1", "asset_type": "agent-plugin"}
    }


def test_retrieval_search_routes_by_agent_asset_type() -> None:
    class Manager:
        def __init__(self) -> None:
            self.groups: list[str] = []

        def is_ready(self, group: str) -> bool:
            self.groups.append(group)
            return True

        def search(self, group: str, query: str, top_k: int, method: str = "embedding") -> list[str]:
            self.groups.append(group)
            return ["asset-1"]

    manager = Manager()
    result = retrieval_search(
        manager,
        "agent-plugin",
        "hello",
        1,
        20,
        asset_type="agent-plugin",
    )

    assert result == ["asset-1"]
    assert manager.groups == ["agent-plugin", "agent-plugin"]


@pytest.mark.parametrize("storage_root", ["agent-plugins", "agent-templates", "agent-mcps"])
def test_agent_index_catalog_path_maps_back_to_asset_id(storage_root: str) -> None:
    record = SimpleNamespace(
        payload="cid-1",
        metadata={
            "skill_path": f"obs://market-bucket/{storage_root}/publisher-1/asset-1/1.0.0/file.zip"
        },
    )
    retriever = SimpleNamespace(
        _loaded_index=SimpleNamespace(catalog_records=(record,)),
    )

    assert _build_cid_to_asset_map(retriever) == {"cid-1": "asset-1"}


def test_agent_index_settings_have_separate_obs_prefixes_and_direct_paths() -> None:
    settings = Settings(_env_file=None)

    assert settings.retrieval_agent_plugin_index_obs_prefix == "agent-plugins-index"
    assert settings.retrieval_agent_template_index_obs_prefix == "agent-templates-index"
    assert settings.retrieval_agent_mcp_index_obs_prefix == "agent-mcps-index"
    assert settings.retrieval_agent_plugin_index_path == ""
    assert settings.retrieval_agent_template_index_path == ""
    assert settings.retrieval_agent_mcp_index_path == ""


def test_rebuild_all_adds_agent_groups_without_changing_existing_groups() -> None:
    class DB:
        def close(self) -> None:
            return None

    storage = SimpleNamespace(config=SimpleNamespace(storage_type="OBS"))
    prefixes = {
        "agent-plugin": "agent-plugins-index",
        "agent-template": "agent-templates-index",
        "agent-mcp": "agent-mcps-index",
    }
    with patch("plugins_market.retrieval.daily_rebuild.rebuild_one_group") as rebuild:
        rebuild_all(
            DB,
            "skills-index",
            "plugins-index",
            storage,
            skip_lock=True,
            run_skill_tag=False,
            agent_prefixes=prefixes,
        )

    assert [(call.args[0], call.args[2]) for call in rebuild.call_args_list] == [
        ("skill", "skills-index"),
        ("plugin", "plugins-index"),
        ("agent-plugin", "agent-plugins-index"),
        ("agent-template", "agent-templates-index"),
        ("agent-mcp", "agent-mcps-index"),
    ]


@pytest.mark.parametrize(
    ("asset_type", "tag_prefix"),
    [
        ("agent-plugin", "agent-plugins-tag"),
        ("agent-template", "agent-templates-tag"),
        ("agent-mcp", "agent-mcps-tag"),
    ],
)
def test_agent_tag_outputs_are_separate(asset_type: str, tag_prefix: str) -> None:
    assert _build_tag_output_uri("market-bucket", asset_type, dir_name="20260911010101") == (
        f"obs://market-bucket/{tag_prefix}/20260911010101"
    )


def test_agent_classification_uses_agent_scanner_and_market_metadata() -> None:
    path = "obs://market-bucket/agent-plugins/publisher-1/asset-1/1.0.0/file.zip"
    metadata = {path: {"asset_id": "asset-1", "asset_type": "agent-plugin", "tags": ["tag"]}}
    storage = SimpleNamespace(config=SimpleNamespace(bucket_name="market-bucket"))
    db = object()
    mapping_text = json.dumps(
        {
            "skill_path": path,
            "root_tag_id": "automation",
            "root_tag_name": "Automation",
        }
    )

    with (
        patch(
            "plugins_market.retrieval.daily_rebuild._load_latest_index_manifest_item_paths",
            return_value=[],
        ),
        patch("plugins_market.retrieval.daily_rebuild._fetch_uncategorized_agent_paths", return_value=set()),
        patch("plugins_market.retrieval.daily_rebuild._read_obs_text", return_value=mapping_text),
        patch("plugins_market.retrieval.daily_rebuild._write_obs_text"),
        patch("plugins_market.retrieval.daily_rebuild._load_previous_tag_snapshot_rows", return_value=[]),
        patch("plugins_market.retrieval.daily_rebuild._refresh_agent_categories_from_mapping") as refresh,
        patch("indexing.workflows.index_builder.IndexBuilder.build_skill_tags") as build_tags,
    ):
        _run_skill_tag_refresh(
            group="agent-plugin",
            db=db,
            storage=storage,
            group_prefix="agent-plugins-index",
            output_tag_uri="obs://market-bucket/agent-plugins-tag/20260911010101",
            current_item_paths=[path],
            build_config=BuildConfig(),
            skill_tag_build_config=None,
            runtime_config=None,
            item_metadata_by_path=metadata,
        )

    assert build_tags.call_args.kwargs["item_type"] == "agent"
    assert build_tags.call_args.kwargs["config"].item_metadata_by_path == metadata
    refresh.assert_called_once_with(
        db,
        "agent-plugin",
        [path],
        {"asset-1": {"category_id": "automation", "category_name": "Automation"}},
    )


def test_agent_category_mapping_only_accepts_its_own_storage_root() -> None:
    text = "\n".join(
        [
            json.dumps(
                {
                    "skill_path": "obs://bucket/agent-plugins/publisher-1/asset-1/1.0.0/file.zip",
                    "root_tag_id": "automation",
                    "root_tag_name": "Automation",
                }
            ),
            json.dumps(
                {
                    "skill_path": "obs://bucket/agent-mcps/publisher-1/wrong/1.0.0/file.zip",
                    "root_tag_id": "connector",
                    "root_tag_name": "Connector",
                }
            ),
        ]
    )

    assert _parse_agent_category_mapping_jsonl(text, "agent-plugin") == {
        "asset-1": {"category_id": "automation", "category_name": "Automation"}
    }


def test_agent_category_refresh_is_scoped_by_asset_and_plugin_type() -> None:
    db = _db()
    wanted = _asset("wanted", asset_type="agent-plugin", plugin_type="agent-plugin")
    stale = _asset("stale", asset_type="agent-plugin", plugin_type="agent-plugin")
    stale.category_id = "old"
    stale.category_name = "Old"
    wrong_group = _asset("wrong", asset_type="agent-mcp", plugin_type="agent-mcp")
    wrong_group.category_id = "keep"
    wrong_group.category_name = "Keep"
    db.add_all([wanted, stale, wrong_group])
    db.commit()
    paths = [
        "obs://bucket/agent-plugins/publisher-1/wanted/1.0.0/file.zip",
        "obs://bucket/agent-plugins/publisher-1/stale/1.0.0/file.zip",
        "obs://bucket/agent-mcps/publisher-1/wrong/1.0.0/file.zip",
    ]

    _refresh_agent_categories_from_mapping(
        db,
        "agent-plugin",
        paths,
        {"wanted": {"category_id": "automation", "category_name": "Automation"}},
    )

    db.expire_all()
    assert db.get(MarketAssetDB, "wanted").category_id == "automation"
    assert db.get(MarketAssetDB, "stale").category_id is None
    assert db.get(MarketAssetDB, "wrong").category_id == "keep"


def test_refresh_agent_tags_runs_each_group_with_its_index_and_metadata() -> None:
    class DB:
        def close(self) -> None:
            return None

    storage = SimpleNamespace(
        config=SimpleNamespace(bucket_name="market-bucket", storage_type="OBS")
    )
    prefixes = {
        "agent-plugin": "agent-plugins-index",
        "agent-template": "agent-templates-index",
        "agent-mcp": "agent-mcps-index",
    }

    def records(_db, group: str, _bucket: str, uri_scheme: str = "obs"):
        path = f"{uri_scheme}://market-bucket/{group}s/publisher-1/{group}/1.0.0/file.zip"
        return [IndexItemRecord(path, {"asset_id": group, "asset_type": group})]

    with (
        patch("plugins_market.retrieval.daily_rebuild._fetch_valid_item_records", side_effect=records),
        patch(
            "plugins_market.retrieval.daily_rebuild.list_index_dirs",
            side_effect=lambda _storage, prefix: [f"{prefix}/20260911010101"],
        ),
        patch("plugins_market.retrieval.daily_rebuild._run_skill_tag_refresh") as refresh,
    ):
        refresh_agent_tags(
            AgentTagRefreshOptions(
                db_factory=DB,
                agent_prefixes=prefixes,
                storage=storage,
                build_config=BuildConfig(),
                skip_lock=True,
            )
        )

    assert [(call.kwargs["group"], call.kwargs["group_prefix"]) for call in refresh.call_args_list] == list(
        prefixes.items()
    )
    assert all(call.kwargs["item_metadata_by_path"] for call in refresh.call_args_list)


def test_agent_index_build_contains_only_common_search_fields(tmp_path) -> None:
    item_dir = tmp_path / "agent-one"
    item_dir.mkdir()
    (item_dir / "plugin.yaml").write_text(
        "name: agent-one\ndisplay_name: Package Name\ndescription: Package description\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "index"
    source_path = str(item_dir.resolve())

    IndexBuilder.build(
        [source_path],
        output_dir,
        item_type="agent",
        config=BuildConfig(
            method=BuildMethod.BM25,
            item_metadata_by_path={
                source_path: {
                    "asset_type": "agent-plugin",
                    "display_name": "Market Name",
                    "short_desc": "Common short",
                    "detail_desc": "Common detail",
                    "tags": ["common-tag"],
                }
            },
        ),
    )

    records = load_catalog_records(output_dir / CATALOG_FILENAME)
    assert len(records) == 1
    assert "Market Name" in records[0].retrieval_text
    assert "Common short" in records[0].retrieval_text
    assert "Common detail" in records[0].retrieval_text
    assert "Package description" in records[0].retrieval_text
    assert "common-tag" in records[0].retrieval_text
    assert load_manifest(output_dir)["item_type"] == "agent"


def test_skill_retrieval_text_does_not_gain_agent_only_detail_field() -> None:
    records = build_catalog_records_from_nodes(
        nodes=[{"worker_id": "skill-one", "cid": "Skills.skill-one"}],
        scanned_skills={
            "skill-one": {
                "name": "Skill One",
                "market_short_desc": "Skill short",
                "market_detail_desc": "AGENT_ONLY_DETAIL_MARKER",
            }
        },
    )

    assert "Skill short" in records[0].retrieval_text
    assert "AGENT_ONLY_DETAIL_MARKER" not in records[0].retrieval_text


@pytest.mark.parametrize("storage_root", ["agent-plugins", "agent-templates", "agent-mcps"])
def test_same_named_agent_assets_from_different_publishers_remain_unique(storage_root: str) -> None:
    first = _unique_dir_name(
        f"obs://bucket/{storage_root}/publisher-a/asset-a/1.0.0/file.zip",
        "same-name",
    )
    second = _unique_dir_name(
        f"obs://bucket/{storage_root}/publisher-b/asset-b/1.0.0/file.zip",
        "same-name",
    )

    assert first == "publisher-a__same-name"
    assert second == "publisher-b__same-name"

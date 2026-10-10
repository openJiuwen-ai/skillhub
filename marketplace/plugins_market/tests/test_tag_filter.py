# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""标签搜索功能测试。

覆盖：
- DB 兜底路径 keyword LIKE 命中 tags 列（cast(tags as char) ilike）。
- 检索索引纳入 tags：build_retrieval_text 拼接标签文本、CatalogRecord.tags 字段。

注意：SQLite 不支持 JSON_CONTAINS，但 cast(tags as char) ilike 在 SQLite 上同样可跑
（SQLite 的 JSON 列存储为文本字符串），故可在此验证 keyword 命中标签文本的逻辑。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import List

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from plugins_market.core.viewer_context import ViewerContext
from plugins_market.models.base import Base
from plugins_market.models.market_assets import MarketAssetDB
from plugins_market.repositories.market_assets_repository import MarketAssetRepository
from plugins_market.schemas.plugin import PluginListQuery
from plugins_market.services.plugin import list_plugins_service

# 本测试文件位于 marketplace/plugins_market/tests/，dispatch 根在 marketplace/dispatch/
DISPATCH_DIR = Path(__file__).resolve().parents[2] / "dispatch"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_cls = sessionmaker(bind=engine)
    return session_cls()


def _viewer(is_admin: bool = False, user_id: str = "") -> ViewerContext:
    return ViewerContext(
        user_id=user_id or None,
        user_login=user_id or None,
        is_system_admin=is_admin,
    )


def _skill(
    asset_id: str,
    *,
    tags: List[str] | None = None,
    name: str | None = None,
    display_name: str | None = None,
    short_desc: str = "",
    plugin_type: str = "skill",
    install_count: int = 0,
) -> MarketAssetDB:
    return MarketAssetDB(
        asset_id=asset_id,
        asset_type="plugin",
        name=name or asset_id,
        display_name=display_name or asset_id,
        publisher_id="owner",
        publisher_name="owner",
        status="PUBLISHED",
        plugin_type=plugin_type,
        latest_version="1.0.0",
        public_latest_version="1.0.0",
        moderation_status="APPROVED",
        view_count=0,
        install_count=install_count,
        like_count=0,
        star_count=0,
        review_count=0,
        average_rating=8.0,
        create_time=1,
        update_time=1,
        tags=tags,
        short_desc=short_desc,
    )


# ---------------------------------------------------------------------------
# DB 兜底路径：keyword LIKE 命中 tags 列
# ---------------------------------------------------------------------------

def test_keyword_search_hits_tag_text():
    """搜索框输入标签文字时，应命中 tags 列里含该标签的 skill。"""
    db = _db()
    db.add(_skill("s1", tags=["python", "cli"], name="my-tool", short_desc="a tool"))
    db.add(_skill("s2", tags=["automation"], name="other", short_desc="another"))
    db.add(_skill("s3", tags=None, name="no-tags", short_desc="no tags here"))
    db.commit()

    repo = MarketAssetRepository(db)
    q = PluginListQuery(plugin_type="skill", search_keyword="python", page=1, page_size=20)
    items, total = repo.list_plugins(q, viewer=_viewer())
    assert total == 1
    assert items[0][0].asset_id == "s1"


def test_keyword_search_hits_tag_not_in_name_or_desc():
    """标签文字只出现在 tags 列、不出现在 name/desc 时也应命中。"""
    db = _db()
    db.add(_skill("s1", tags=["swarmskill"], name="verify-swarm", short_desc="a team skill"))
    db.add(_skill("s2", tags=["imported"], name="pptx-helper", short_desc="pptx tool"))
    db.commit()

    repo = MarketAssetRepository(db)
    q = PluginListQuery(plugin_type="skill", search_keyword="swarmskill", page=1, page_size=20)
    items, total = repo.list_plugins(q, viewer=_viewer())
    assert total == 1
    assert items[0][0].asset_id == "s1"


def test_keyword_search_still_hits_name_and_desc():
    """keyword 搜索原有的 name/display_name/short_desc 命中不受影响。"""
    db = _db()
    db.add(_skill("s1", tags=None, name="python-runner", short_desc="runs python code"))
    db.add(_skill("s2", tags=["java"], name="other", short_desc="something else"))
    db.commit()

    repo = MarketAssetRepository(db)
    q = PluginListQuery(plugin_type="skill", search_keyword="python", page=1, page_size=20)
    items, total = repo.list_plugins(q, viewer=_viewer())
    assert total == 1
    assert items[0][0].asset_id == "s1"


def test_keyword_search_empty_tags_not_matched():
    """tags 为 NULL 的 skill 不应被标签关键词命中。"""
    db = _db()
    db.add(_skill("s1", tags=None, name="alpha", short_desc="desc"))
    db.commit()

    repo = MarketAssetRepository(db)
    q = PluginListQuery(plugin_type="skill", search_keyword="python", page=1, page_size=20)
    items, total = repo.list_plugins(q, viewer=_viewer())
    assert total == 0


def test_keyword_search_multi_tag_phrase():
    """归一化投影后，跨标签关键词（空格分隔相邻标签）也应命中。"""
    db = _db()
    db.add(_skill("s1", tags=["python", "cli"], name="runner", short_desc="a tool"))
    db.commit()

    repo = MarketAssetRepository(db)
    q = PluginListQuery(plugin_type="skill", search_keyword="python cli", page=1, page_size=20)
    items, total = repo.list_plugins(q, viewer=_viewer())
    assert total == 1
    assert items[0][0].asset_id == "s1"


def test_keyword_search_tag_with_json_escaped_chars():
    """含双引号/反斜线的标签经 JSON 转义（\\" -> \\"，\\ -> \\\\）后，投影应还原为原始字符。

    SQLite/MySQL 的 JSON 文本序列化同样输出 \\" 与 \\\\，故可在内存库验证：
    若 REPLACE 链未解码转义，say"hello 会投影成 say\\hello，关键词再也无法命中。
    """
    db = _db()
    db.add(_skill("s1", tags=['say"hello', "back\\slash"], name="q1", short_desc="d"))
    db.add(_skill("s2", tags=["plain"], name="q2", short_desc="d"))
    db.commit()

    repo = MarketAssetRepository(db)
    for kw in ('say"hello', "back\\slash"):
        q = PluginListQuery(plugin_type="skill", search_keyword=kw, page=1, page_size=20)
        items, total = repo.list_plugins(q, viewer=_viewer())
        assert total == 1
        assert items[0][0].asset_id == "s1"


def test_grantable_skill_keyword_search_hits_tags():
    """授权技能搜索（publisher 的私有 skill）的 keyword 过滤同样命中 tags 列。"""
    db = _db()
    db.add(_skill("s1", tags=["swarmskill"], name="team-a", short_desc="private team skill"))
    db.query(MarketAssetDB).filter(MarketAssetDB.asset_id == "s1").update(
        {"visibility": "private"}
    )
    db.commit()

    repo = MarketAssetRepository(db)
    rows, total = repo.search_grantable_skills_for_publisher(
        publisher_id="owner", keyword="swarmskill", page=1, page_size=20
    )
    assert total == 1
    assert rows[0].asset_id == "s1"


# ---------------------------------------------------------------------------
# 检索索引纳入 tags：build_retrieval_text + CatalogRecord
# ---------------------------------------------------------------------------

def test_build_retrieval_text_includes_tags():
    """build_retrieval_text 应将 tags 拼入检索文本，使 BM25/embedding 能命中标签。"""
    import retrieval  # noqa: F401  -- 注册 sys.path 使 bare import 可用
    from indexing.workflows.artifacts import build_retrieval_text

    text = build_retrieval_text(
        skill_id="alpha",
        name="my-skill",
        description="a skill for testing",
        content="",
        cid="Skills.Alpha",
        tags=["python", "cli", "automation"],
    )
    assert "python" in text
    assert "cli" in text
    assert "automation" in text


def test_build_retrieval_text_without_tags():
    """不传 tags 时 build_retrieval_text 应正常工作（向后兼容）。"""
    import retrieval  # noqa: F401
    from indexing.workflows.artifacts import build_retrieval_text

    text = build_retrieval_text(
        skill_id="alpha",
        name="my-skill",
        description="a skill for testing",
        content="",
        cid="Skills.Alpha",
    )
    assert "my-skill" in text
    assert "a skill for testing" in text


def test_catalog_record_has_tags_field():
    """CatalogRecord 应有 tags 字段，默认空元组（向后兼容）。"""
    import retrieval  # noqa: F401
    from indexing.catalog.records import CatalogRecord

    record = CatalogRecord(
        skill_id="alpha",
        worker_id="alpha",
        cid="Skills.Alpha",
        name="alpha",
        description="test",
        skill_path="/alpha",
        branch_path="Skills",
        category="test",
        retrieval_text="alpha test",
        metadata={},
    )
    assert record.tags == ()


def test_scanned_item_has_tags_field():
    """ScannedItem 应有 tags 字段，默认空列表。"""
    import retrieval  # noqa: F401
    from indexing.scanners.base import ScannedItem

    item = ScannedItem(id="alpha", name="alpha", description="test", item_path="/alpha")
    assert item.tags == []
    d = item.to_dict()
    assert "tags" in d
    assert d["tags"] == []


def test_extract_tags_from_metadata():
    """extract_tags_from_metadata 应从 plugin.yaml metadata 提取干净标签。"""
    import retrieval  # noqa: F401
    from indexing.scanners.common import extract_tags_from_metadata

    tags = extract_tags_from_metadata({"tags": ["python", " cli ", "", "automation"]})
    assert tags == ["python", "cli", "automation"]

    assert extract_tags_from_metadata({}) == []
    assert extract_tags_from_metadata({"tags": "not-a-list"}) == []
    assert extract_tags_from_metadata({"tags": []}) == []


# ---------------------------------------------------------------------------
# 标签精确过滤：tags / tags_match 参数
# ---------------------------------------------------------------------------

def test_tags_match_query_validation():
    """tags_match 只接受 all/any（大小写不敏感），非法值应报错。"""
    import pytest
    from pydantic import ValidationError

    assert PluginListQuery(tags="python", tags_match="ANY").tags_match == "any"
    assert PluginListQuery(tags="python").tags_match == "all"
    with pytest.raises(ValidationError):
        PluginListQuery(tags="python", tags_match="maybe")


def test_retrieval_path_ignores_tags_when_keyword_present(monkeypatch):
    """搜索时不感知 tags：检索路径返回混合标签/无标签资产，不做标签过滤。

    标签是浏览态过滤器，不与关键词搜索组合（前端置灰标签行，服务端兜底剥离）。
    打分顺序 s1(无标签) 在前，若误做标签过滤 s1 会被剔除、断言失败。
    """
    from plugins_market.services.plugin import retrieval_search as svc_retrieval_search
    from plugins_market.services.plugin import get_index_manager

    db = _db()
    db.add(_skill("s1", tags=None, name="alpha", short_desc="kw hit"))
    db.add(_skill("s2", tags=["python"], name="beta", short_desc="kw hit"))
    db.commit()

    ids = ["s1", "s2"]
    monkeypatch.setattr(
        "plugins_market.services.plugin.retrieval_search",
        lambda *a, **kw: list(ids),
    )
    monkeypatch.setattr("plugins_market.services.plugin.get_index_manager", lambda: None)

    q = PluginListQuery(
        plugin_type="skill",
        search_keyword="hit",
        tags="python",
        tags_match="all",
        page=1,
        page_size=20,
    )
    resp = list_plugins_service(q, db, None, viewer=_viewer(), use_retrieval_search=True)
    assert [it.asset_id for it in resp.items] == ["s1", "s2"]
    assert resp.total == 2


def test_retrieval_returns_none_falls_back_to_db_like(monkeypatch):
    """检索返回 None（不可用/出错）-> 回退 DB LIKE，守 retrieval_search 契约。

    None 是 retrieval_search 表示「我没法回答」的信号（索引未就绪/检索异常），此时落到
    repo.list_plugins 走子串 LIKE 召回是对的：检索根本没给答案，不该返回空页。
    """
    monkeypatch.setattr(
        "plugins_market.services.plugin.retrieval_search",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr("plugins_market.services.plugin.get_index_manager", lambda: None)

    db = _db()
    db.add(_skill("s1", tags=None, name="kw-hit", short_desc="d"))
    db.add(_skill("s2", tags=None, name="zzz-other", short_desc="d"))
    db.commit()

    q = PluginListQuery(plugin_type="skill", search_keyword="kw-hit", page=1, page_size=20)
    resp = list_plugins_service(q, db, None, viewer=_viewer(), use_retrieval_search=True)
    # DB LIKE 兜底命中 s1（name 含 kw-hit）；s2 不含该子串，不命中
    assert [it.asset_id for it in resp.items] == ["s1"]
    assert resp.total == 1


def test_retrieval_returns_empty_returns_empty_page(monkeypatch):
    """检索返回 []（确认无命中）-> 返回空页，不回退 DB LIKE，守契约、保精度。

    这是上一版 if item_ids:（真值判断）把 [] 当 None 处理导致的精度回退修复点：
    检索正确判无匹配时，不应再跑 DB LIKE 翻出 name/short_desc 子串命中但语义无关的资产。
    s1 的 name 含关键词，DB LIKE 本会命中--若误回退就会出现在结果里，断言会失败。
    """
    monkeypatch.setattr(
        "plugins_market.services.plugin.retrieval_search",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr("plugins_market.services.plugin.get_index_manager", lambda: None)

    db = _db()
    db.add(_skill("s1", tags=None, name="kw-hit", short_desc="d"))
    db.commit()

    q = PluginListQuery(plugin_type="skill", search_keyword="kw-hit", page=1, page_size=20)
    resp = list_plugins_service(q, db, None, viewer=_viewer(), use_retrieval_search=True)
    # 检索确认无命中 -> 空页，s1 不被 DB LIKE 翻出
    assert resp.items == []
    assert resp.total == 0


def test_retrieval_hits_filtered_out_returns_empty_page(monkeypatch):
    """检索有命中但全被类目过滤 -> 返回空页，不退化为 DB LIKE 跨过滤召回（见评审意见 3）。

    过滤是用户筛选条件所致，属合法结果；返回空页而非跨过滤 LIKE 召回，避免在他类目
    召回不相关结果。s1 在 catA、query 指定 catB -> 命中被过滤 -> 空页。
    """
    monkeypatch.setattr(
        "plugins_market.services.plugin.retrieval_search",
        lambda *a, **kw: ["s1"],
    )
    monkeypatch.setattr("plugins_market.services.plugin.get_index_manager", lambda: None)

    db = _db()
    s1 = _skill("s1", tags=None, name="kw-hit", short_desc="d")
    s1.category_id = "catA"
    db.add(s1)
    db.commit()

    q = PluginListQuery(
        plugin_type="skill",
        search_keyword="kw-hit",
        category_id="catB",
        page=1,
        page_size=20,
    )
    resp = list_plugins_service(q, db, None, viewer=_viewer(), use_retrieval_search=True)
    assert resp.items == []
    assert resp.total == 0


def test_parse_tag_filter():
    """parse_tag_filter 应按逗号切分、strip、去重、保序，且数量截断到 QUERY_TAGS_MAX_COUNT。"""
    from plugins_market.repositories.market_assets_repository import parse_tag_filter
    from plugins_market.validation.constants import QUERY_TAGS_MAX_COUNT

    assert parse_tag_filter(None) == []
    assert parse_tag_filter("") == []
    assert parse_tag_filter("python, cli ,python") == ["python", "cli"]

    # 与发布校验侧同口径归一化：NFKC 折全角->半角、casefold 统一大小写 + 去重变体，
    # 保证 tags=AI / tags=ＡＩ（全角）等直接 API 调用能命中 DB 中的小写半角标签
    assert parse_tag_filter("AI,\uff21\uff29,ai,\uff30\uff39,py") == ["ai", "py"]

    # 数量上限：超出部分静默截断（每个标签生成一个 JSON_CONTAINS 条件，防查询计划膨胀）
    many = parse_tag_filter(",".join(f"t{i}" for i in range(QUERY_TAGS_MAX_COUNT * 3)))
    assert many == [f"t{i}" for i in range(QUERY_TAGS_MAX_COUNT)]


def test_prepare_tag_keyword_for_like():
    """_prepare_tag_keyword_for_like：NFKC 折全角->半角 + 转义 LIKE 通配符 %/_/\\。

    ilike 已不区分大小写，故不做 casefold；空白/None -> ''（调用方据此跳过过滤）。
    覆盖两条评审意见：全角输入（ＰＹ）经 NFKC 折半角后能匹配 DB 半角标签；
    标签名内的 _ / % 被转义为字面量（escape='\\'），不再当通配符误匹配。
    """
    from plugins_market.repositories.market_assets_repository import (
        _prepare_tag_keyword_for_like,
    )

    # None / 空 / 纯空白 -> 空串（调用方据此跳过 ilike 过滤）
    assert _prepare_tag_keyword_for_like(None) == ""
    assert _prepare_tag_keyword_for_like("") == ""
    assert _prepare_tag_keyword_for_like("   ") == ""

    # NFKC：全角字母折半角（utf8mb4_general_ci 下不等价，ilike 直查会空）
    assert _prepare_tag_keyword_for_like("\uff30\uff39") == "PY"  # 全角 PY -> PY
    # 全角通配符先折叠再转义：％ U+FF05 -> % -> \%，＿ U+FF3F -> _ -> \_
    assert _prepare_tag_keyword_for_like("\uff30\uff05\uff3f") == "P\\%\\_"

    # 半角通配符转义为字面量（标签名可含 _，如 data_science；含 %，如 100%）
    assert _prepare_tag_keyword_for_like("data_science") == "data\\_science"
    assert _prepare_tag_keyword_for_like("100%") == "100\\%"
    # 反斜杠先转义，避免把后续引入的 \% \_ 再转一次
    assert _prepare_tag_keyword_for_like("a\\b") == "a\\\\b"

    # strip 前后空白
    assert _prepare_tag_keyword_for_like("  py  ") == "py"


def test_plugin_list_query_tags_max_length():
    """tags 查询参数长度上限 QUERY_TAGS_MAX_LEN：超长直接校验失败（FastAPI 层 422）。"""
    import pytest
    from pydantic import ValidationError

    from plugins_market.validation.constants import QUERY_TAGS_MAX_LEN

    ok_len = QUERY_TAGS_MAX_LEN - len(",") * 19  # 20 个标签的合法参数
    PluginListQuery(tags="a" * max(ok_len, 1))  # 不抛异常即可

    with pytest.raises(ValidationError) as exc_info:
        PluginListQuery(tags="x" * (QUERY_TAGS_MAX_LEN + 1))
    assert exc_info.value.errors()[0]["type"] == "string_too_long"


def test_extract_tags_from_metadata_rejects_comma_tags():
    """扫描期提取应剔除含逗号的标签（与发布校验一致，避免破坏多标签分隔协议）。"""
    import retrieval  # noqa: F401
    from indexing.scanners.common import extract_tags_from_metadata

    tags = extract_tags_from_metadata({"tags": ["python", "data,analysis", " cli ", "", "automation"]})
    assert tags == ["python", "cli", "automation"]


def test_dispatch_extract_tags_from_metadata_rejects_comma_tags():
    """dispatch 侧扫描提取与 retrieval 侧同口径：剔除含逗号的标签。

    两套并行实现若口径不一致，旁路数据（直接 DB 写入/旧数据迁移）会造成
    dispatch 与 retrieval 索引对同一资产产出不同 tags，检索结果分叉。
    """
    import retrieval  # noqa: F401
    from indexing.scanners.common import extract_tags_from_metadata as retrieval_impl

    # spec_from_file_location 按文件路径直接加载，不经过 sys.path，
    # 也避免 dispatch/retrieval 两棵目录下同名包 indexing 的模块重名问题
    dispatch_spec = importlib.util.spec_from_file_location(
        "dispatch_scanners_common",
        DISPATCH_DIR / "indexing" / "scanners" / "common.py",
    )
    dispatch_module = importlib.util.module_from_spec(dispatch_spec)
    dispatch_spec.loader.exec_module(dispatch_module)
    dispatch_impl = dispatch_module.extract_tags_from_metadata

    sample = {"tags": ["python", "data,analysis", " cli ", "", "automation"]}
    assert dispatch_impl(sample) == ["python", "cli", "automation"]
    assert dispatch_impl(sample) == retrieval_impl(sample)
    assert dispatch_impl({}) == [] and dispatch_impl({"tags": "x"}) == []

    # \uff30\uff39 / \uff23\uff2c\uff29 为全角字母，NFKC 折半角后 casefold；
    # 与发布校验侧同口径归一化 + 去重变体，保证索引与 DB 入库形式一致
    norm_sample = {"tags": ["PY", "\uff30\uff39", "py", "\uff23\uff2c\uff29", "cli", "a,b"]}
    assert dispatch_impl(norm_sample) == retrieval_impl(norm_sample) == ["py", "cli"]


def test_tags_text_column_compiles_to_char4096_on_mysql():
    """_tags_text_column 在 MySQL 方言应编译为显式 CHAR(4096)，避免无长度 CHAR 的截断风险。"""
    from sqlalchemy.dialects import mysql

    from plugins_market.repositories.market_assets_repository import _tags_text_column

    compiled = str(_tags_text_column().compile(dialect=mysql.dialect()))
    assert "CHAR(4096)" in compiled


def test_publish_validation_rejects_comma_tag():
    """发布校验应拒收含逗号的标签（多标签过滤参数以逗号分隔，协议无法表达）。"""
    import pytest

    from plugins_market.core.errors import PublishError
    from plugins_market.validation.plugin_yaml import validate_plugin_yaml_public

    base = {
        "name": "comma-tag-skill",
        "display_name": "Comma Tag Skill",
        "description": "skill with comma tag",
        "runtime": {"type": "skill"},
        "metadata": {"author": "owner"},
    }
    ok = dict(base, metadata={"author": "owner", "tags": ["python", "cli"]})
    assert validate_plugin_yaml_public(ok).tags == ["python", "cli"]

    bad = dict(base, metadata={"author": "owner", "tags": ["python", "data,analysis"]})
    with pytest.raises(PublishError) as exc_info:
        validate_plugin_yaml_public(bad)
    assert "逗号" in exc_info.value.message


def test_publish_validation_normalizes_and_dedups_tags():
    """发布校验对标签做 NFKC + casefold 归一并去重，挡住大小写/全半角变体的垃圾标签。

    纯中文在 NFKC/casefold 下恒等，不会误删或改写；重复（含变体）静默跳过、不占名额。
    """
    from plugins_market.validation.plugin_yaml import validate_plugin_yaml_public

    data = {
        "name": "norm-tags-skill",
        "display_name": "Norm Tags Skill",
        "description": "tags normalization",
        "runtime": {"type": "skill"},
        "metadata": {
            "author": "owner",
            "tags": [
                "Python",                          # -> python
                "PYTHON",                          # -> python（大小写变体，去重）
                "\uff50\uff59\uff54\uff48\uff4f\uff4e",  # 全角 python -> python（NFKC 折半角，去重）
                "机器学习",                          # 纯中文，归一恒等
                "机器学习",                          # 重复，去重
                "cli",                             # 原样
            ],
        },
    }
    assert validate_plugin_yaml_public(data).tags == ["python", "机器学习", "cli"]


def test_publish_validation_rejects_fullwidth_comma_tag():
    """全角逗号经 NFKC 折成半角后同样拒收（与半角逗号规则一致，不破坏过滤协议）。"""
    import pytest

    from plugins_market.core.errors import PublishError
    from plugins_market.validation.plugin_yaml import validate_plugin_yaml_public

    data = {
        "name": "fw-comma-skill",
        "display_name": "FW Comma Skill",
        "description": "fullwidth comma tag",
        "runtime": {"type": "skill"},
        "metadata": {"author": "owner", "tags": ["数据\uff0c分析"]},
    }
    with pytest.raises(PublishError) as exc_info:
        validate_plugin_yaml_public(data)
    assert "逗号" in exc_info.value.message


def test_list_plugin_tags_skips_featured_tags_with_zero_count():
    """MARKET_FEATURED_TAGS 中无可见资产使用的标签（count=0）不应展示，避免点击后空结果。"""
    import asyncio
    from unittest.mock import patch

    from plugins_market.routers.plugin import list_plugin_tags

    class _FakeRepo:
        def __init__(self, counts: dict):
            self._counts = counts

        def list_tag_options(self, *, plugin_type=None, keyword=None, limit=20):
            return list(self._counts.items())

    class _FakeSettings:
        market_featured_tags = "ghost-tag, python, cli, ghost-tag"

    async def _run():
        with patch(
            "plugins_market.routers.plugin.MarketAssetRepository",
            return_value=_FakeRepo({"python": 5, "cli": 2, "auto": 9}),
        ), patch(
            "plugins_market.routers.plugin.settings",
            _FakeSettings(),
        ), patch(
            "plugins_market.routers.plugin.cache_get",
            return_value=None,
        ), patch(
            "plugins_market.routers.plugin.cache_set",
        ):
            res = await list_plugin_tags(db=None, plugin_type="skill", limit=10)
        return res

    res = asyncio.run(_run())
    ordered = [(t.tag, t.count) for t in res.data]
    # ghost-tag 不在 count_map（count=0）被跳过；存在的 featured 按配置顺序排前，其余按次数补齐
    assert ordered == [("python", 5), ("cli", 2), ("auto", 9)]
    assert all(t.count > 0 for t in res.data)


def test_list_plugin_tags_keyword_mode_skips_featured():
    """keyword 在场时走子串搜索分支：跳过运营置顶，按 repo 返回的 count 降序原序。"""
    import asyncio
    from unittest.mock import patch

    from plugins_market.routers.plugin import list_plugin_tags

    class _FakeRepo:
        def __init__(self, counts: dict):
            self._counts = counts
            self.last_kwargs = None

        def list_tag_options(self, *, plugin_type=None, keyword=None, limit=20):
            self.last_kwargs = {"plugin_type": plugin_type, "keyword": keyword, "limit": limit}
            items = list(self._counts.items())
            if keyword:
                kw = keyword.lower()
                items = [(t, c) for t, c in items if kw in t.lower()]
            items.sort(key=lambda kv: (-kv[1], kv[0]))
            return items[:limit]

    class _FakeSettings:
        market_featured_tags = "python, cli"

    async def _run():
        fake = _FakeRepo({"python": 5, "pypy": 8, "cli": 2, "rust": 1})
        with patch(
            "plugins_market.routers.plugin.MarketAssetRepository",
            return_value=fake,
        ), patch(
            "plugins_market.routers.plugin.settings",
            _FakeSettings(),
        ), patch(
            "plugins_market.routers.plugin.cache_get",
            return_value=None,
        ), patch(
            "plugins_market.routers.plugin.cache_set",
        ):
            res = await list_plugin_tags(db=None, plugin_type="skill", limit=50, keyword="py")
        return res, fake

    res, fake = asyncio.run(_run())
    ordered = [(t.tag, t.count) for t in res.data]
    # 走了 keyword 分支（keyword 透传给 repo、limit 透传），没走默认 featured 分支
    assert fake.last_kwargs == {"plugin_type": "skill", "keyword": "py", "limit": 50}
    # featured 配置了 python/cli，但 keyword 模式不重排，按 repo count 降序原序返回匹配项
    assert ordered == [("pypy", 8), ("python", 5)]


def test_list_tag_options_sqlite_returns_empty():
    """SQLite 无 JSON_TABLE，list_tag_options 应安全返回空列表（不抛异常）。"""
    db = _db()
    db.add(_skill("s1", tags=["python", "cli"]))
    db.add(_skill("s2", tags=["python"]))
    db.commit()

    repo = MarketAssetRepository(db)
    assert repo.list_tag_options(limit=10) == []


def test_db_list_plugins_with_tags_param_sqlite_skips_json_contains():
    """SQLite 上 tags 参数过滤跳过（无 JSON_CONTAINS），不抛异常，退化为无该过滤。

    真实过滤语义由 MySQL 集成环境与检索路径 Python 过滤保证。
    """
    db = _db()
    db.add(_skill("s1", tags=["python"]))
    db.add(_skill("s2", tags=["java"]))
    db.commit()

    repo = MarketAssetRepository(db)
    q = PluginListQuery(plugin_type="skill", tags="python", tags_match="all", page=1, page_size=20)
    items, total = repo.list_plugins(q, viewer=_viewer())
    # SQLite 无 JSON_CONTAINS：过滤不生效，两行都返回（MySQL 上会只剩 s1）
    assert total == 2


# ---------------------------------------------------------------------------
# recommend 路径：带 tags 时降级 DB 排序路径（防召回窗口截断 / total 失真）
# ---------------------------------------------------------------------------

class _StubRecommendItem:
    """recommender.online.types.RecommendItem 的替身，只用到 asset_id 字段。"""

    def __init__(self, asset_id: str):
        self.asset_id = asset_id
        self.score = 1.0


def _run_recommend_path(db, monkeypatch, *, tags: str | None, tags_match: str = "all", rows):
    """走 list_plugins_service 的 recommend 分支并返回 (asset_ids, total)。

    service 内是函数局部 import，必须 patch 源模块；同时打开 recommender_enabled
    （默认关闭，不开会直接落到 install_count 排序路径）。
    apply_recommender_settings_to_env 会写 Milvus/Redis 环境变量，一并替换。
    """
    from plugins_market.core.config import settings
    from plugins_market.recommender import bootstrap, service as rec_service

    for row in rows:
        db.add(row)
    db.commit()

    rec_ids = [r.asset_id for r in rows]
    monkeypatch.setattr(settings, "recommender_enabled", True)
    monkeypatch.setattr(bootstrap, "apply_recommender_settings_to_env", lambda: None)
    monkeypatch.setattr(
        rec_service, "run_recommend_for_user",
        lambda **kw: ([_StubRecommendItem(i) for i in rec_ids], "test"),
    )

    query = PluginListQuery(
        plugin_type="skill",
        order_by="recommend",
        tags=tags,
        tags_match=tags_match,
        page=1,
        page_size=20,
    )
    resp = list_plugins_service(
        query,
        db,
        None,  # S3StorageClient：路径为 None 时 icon_uri 直接返回 None，不触 S3
        viewer=_viewer(user_id="u1"),
        use_retrieval_search=True,
    )
    return [it.asset_id for it in resp.items], resp.total


def test_recommend_path_without_tags_unchanged_keeps_recall(monkeypatch):
    """不带 tags 时推荐路径照旧（进入召回窗口，由打分顺序决定返回）。"""
    db = _db()
    ids, total = _run_recommend_path(
        db,
        monkeypatch,
        tags=None,
        rows=[
            _skill("s1", tags=["python"], install_count=1),
            _skill("s2", tags=None, install_count=9),
        ],
    )
    # 推荐打分顺序原样保留（s1 在前），而非 install_count 降序
    assert ids == ["s1", "s2"]
    assert total == 2


def test_recommend_path_blank_tags_still_recommends(monkeypatch):
    """tags 传了但解析后为空（纯空白/逗号）：不触发降级，照常走推荐。"""
    db = _db()
    ids, _total = _run_recommend_path(
        db,
        monkeypatch,
        tags=" , ",
        rows=[
            _skill("s1", tags=["python"], install_count=1),
            _skill("s2", tags=None, install_count=9),
        ],
    )
    # 推荐打分顺序保留（s1 在前）
    assert ids == ["s1", "s2"]


def test_keyword_search_strips_tags_before_db_fallback():
    """关键词 + tags 走 DB 兜底时同样忽略 tags：LIKE 搜索不受标签参数影响。

    服务入口处把 tags 置 None 后才进检索/DB 路径，SQLite 上 JSON_CONTAINS
    本就跳过，此用例断言的是剥离后 tags 参数不再参与任何过滤。
    """
    db = _db()
    db.add(_skill("s1", tags=None, name="kw-hit", short_desc="d"))
    db.add(_skill("s2", tags=["python"], name="kw-hit-2", short_desc="d"))
    db.commit()

    q = PluginListQuery(
        plugin_type="skill",
        search_keyword="kw-hit",
        tags="python",
        tags_match="all",
        page=1,
        page_size=20,
    )
    resp = list_plugins_service(q, db, None, viewer=_viewer(), use_retrieval_search=False)
    # LIKE 前缀命中 s1/s2 两条；tags 已被剥离，不会把无标签的 s1 滤掉
    assert {it.asset_id for it in resp.items} == {"s1", "s2"}
    assert resp.total == 2


def test_recommend_path_with_tags_falls_back_to_db(monkeypatch):
    """带 tags 时不进推荐召回窗口（防截断/total 失真），降级 DB 排序路径。

    推荐打分函数被替换为「若被调用则失败」：断言 run_recommend_for_user 不被调用。
    SQLite 无 JSON_CONTAINS，tags 过滤跳过，但排序路径应为 install_count。
    """
    from plugins_market.core.config import settings
    from plugins_market.recommender import service as rec_service

    db = _db()
    db.add(_skill("s1", tags=["python"], install_count=5))
    db.add(_skill("s2", tags=["python"], install_count=9))
    db.commit()

    def _must_not_call(**kw):
        raise AssertionError("run_recommend_for_user must not be called when tags present")

    monkeypatch.setattr(settings, "recommender_enabled", True)
    monkeypatch.setattr(rec_service, "run_recommend_for_user", _must_not_call)

    query = PluginListQuery(
        plugin_type="skill",
        order_by="recommend",
        tags="python",
        tags_match="all",
        page=1,
        page_size=20,
    )
    resp = list_plugins_service(query, db, None, viewer=_viewer(user_id="u1"))
    # install_count 降序：s2(9) 在前；推荐未被调用
    assert [it.asset_id for it in resp.items] == ["s2", "s1"]


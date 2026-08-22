# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from collections import defaultdict
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
import json
import time
import unicodedata

from sqlalchemy import and_, asc, desc, func, or_, case, exists, select, not_, cast, Text, text, literal_column
from sqlalchemy.sql.sqltypes import CHAR
from sqlalchemy.orm import Session

from plugins_market.models.market_assets import (
    MarketAssetDB,
    MarketAssetVersionDB,
    MarketSkillReviewDB,
    PluginFetchRecordDB,
    MarketAssetInteractionDB,
)
from plugins_market.models.groups import MarketGroupDB, MarketGroupMemberDB, MarketGroupSkillGrantDB
from plugins_market.core.moderation import (
    MODERATION_APPROVED,
    MODERATION_PENDING,
    MODERATION_REJECTED,
    SKILL_LIKE_PLUGIN_TYPES,
    is_skill_like_plugin_type,
)
from plugins_market.schemas.plugin import AssetCreate, AssetVersionCreate, PluginListQuery
from plugins_market.validation.constants import QUERY_TAGS_MAX_COUNT
from .base_repository import MarketBaseRepository

if TYPE_CHECKING:
    from plugins_market.core.viewer_context import ViewerContext


def _tags_text_column():
    """tags JSON 列的文本投影：cast 为文本后剥离 JSON 结构字符并还原转义，标签间归一为单空格。

    MySQL 存 JSON 数组为 ["a", "b"]，且按 JSON 规范转义值内特殊字符：
    双引号 -> \\"，反斜线 -> \\\\。处理顺序必须是「先转义占位、再剥结构、最后还原」：
    1. 把转义序列 \\\\ 和 \\" 先替换为占位符（此时剩余引号必为结构性引号）；
    2. 剥离数组括号/分隔逗号/结构性引号，标签间归一为单空格，跨标签关键词可命中；
    3. 占位符还原为真实字符——否则含双引号/反斜线的标签会被损坏
       （如 say"hello 投影成 say\\hello，关键词再也无法命中）。
    SQL NULL 的行 cast 后仍为 NULL，ilike 不匹配。
    显式 CAST(... AS CHAR(4096))：tags 上限为 32 个 x 64 字符（发布校验限制），
    4096 足够容纳；不带长度的 CAST AS CHAR 在部分 MySQL 版本/中间件上可能截断。
    """
    json_text_type = Text().with_variant(CHAR(4096), "mysql", "mariadb")
    col = cast(MarketAssetDB.tags, json_text_type)
    # 1) JSON 转义序列 -> 控制字符占位符（若先剥引号，\" 会残留反斜线且无法再区分）
    decoded = func.replace(col, "\\\\", "\x01")
    decoded = func.replace(decoded, '\\"', "\x02")
    # 2) 剥离结构性引号/逗号
    stripped = func.replace(decoded, '", "', " ")
    stripped = func.replace(stripped, '",', " ")
    stripped = func.replace(stripped, ',"', " ")
    stripped = func.replace(stripped, '["', "")
    stripped = func.replace(stripped, '"]', "")
    stripped = func.replace(stripped, '"', "")
    # 3) 占位符 -> 真实字符
    restored = func.replace(stripped, "\x01", "\\")
    restored = func.replace(restored, "\x02", '"')
    return func.replace(func.replace(restored, "  ", " "), "  ", " ")

# SQL IN()/NOT IN() 时需要列表形式
_SKILL_LIKE_PLUGIN_TYPES_LIST = tuple(SKILL_LIKE_PLUGIN_TYPES)


def parse_tag_filter(tags: str | None) -> list[str]:
    """逗号分隔的标签参数 -> 去重后的标签列表（保序，最多 QUERY_TAGS_MAX_COUNT 个）。

    与发布校验侧（validation/plugin_yaml.py）同口径归一化：NFKC 折全角->半角、
    casefold 统一大小写。DB 标签已按归一化形式存入，此处不归一化会让 tags=AI、
    tags=ＡＩ（全角）等直接 API 调用因 JSON_CONTAINS 大小写/全半角不匹配而静默空结果。
    含逗号的标签无法在该协议下表达（发布校验已拒收），此处剔除以防旁路数据。
    数量截断（不报错）：每个标签会生成一个 JSON_CONTAINS 条件，数百个会拖垮查询计划；
    正常前端最多选十几个 chip，QUERY_TAGS_MAX_COUNT 与标签聚合接口的 limit 同量级。
    """
    if not tags:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for part in tags.split(","):
        t = unicodedata.normalize("NFKC", part.strip()).casefold()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
            if len(out) >= QUERY_TAGS_MAX_COUNT:
                break
    return out


def _prepare_tag_keyword_for_like(keyword: str | None) -> str:
    """归一化并转义标签搜索 keyword，供 ilike 模式内插（配合 escape='\\'）。

    与发布校验/parse_tag_filter 同口径 NFKC 折全角->半角：全角字母（如 ＰＹ）在
    utf8mb4_general_ci 下不等价于半角，ilike 直查会空。ilike 已不区分大小写，故不做
    casefold（与 parse_tag_filter 的 JSON_CONTAINS 精确匹配路径不同）。
    标签名可含 _ 或 %（发布校验仅禁逗号），它们在 LIKE 中是通配符，须转义为字面量：
    反斜杠先转义，避免把后续引入的 \\% \\_ 再转一次。空白/None -> ''（调用方据此跳过过滤）。
    """
    kw = unicodedata.normalize("NFKC", (keyword or "").strip())
    if not kw:
        return ""
    return kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _anonymous_viewer() -> "ViewerContext":
    """聚合接口按匿名访客口径计数（避免 TYPE_CHECKING 循环导入，函数内导入）。"""
    from plugins_market.core.viewer_context import ViewerContext

    return ViewerContext(user_id=None, user_login=None, is_system_admin=False)


def pending_moderation_version_filter():
    """MarketAssetVersionDB 行级：是否处于待审（含 publish_result / reviewing）。"""
    return or_(
        MarketAssetVersionDB.publish_result.in_(("pending_moderation", "reviewing")),
        and_(
            or_(
                MarketAssetVersionDB.publish_result.is_(None),
                MarketAssetVersionDB.publish_result == "",
            ),
            MarketAssetVersionDB.moderation_status == MODERATION_PENDING,
        ),
    )


def pending_moderation_version_exists_for_asset():
    """资产是否存在待审版本（与待审 Tab / 列表 public_ok 判定一致）。"""
    return exists(
        select(1)
        .select_from(MarketAssetVersionDB)
        .where(
            MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
            pending_moderation_version_filter(),
        )
        .correlate(MarketAssetDB)
    )


def _version_pending_moderation_exists():
    """版本待审：含 publish_result 或显式 moderation_status=PENDING。"""
    return pending_moderation_version_exists_for_asset()


def skill_moderation_list_clause(
    viewer: "ViewerContext",
    *,
    publisher_scoped: bool = False,
    moderation_queue_scoped: bool = False,
):
    """Skill 列表/检索可见性。

    - 公开市场（首页、搜索、未带本人 publisher_id）：仅展示已通过审核的 Skill。
    - 个人「我的 Skills」（publisher_id 筛选且为本人）：展示本人全部状态。
    - 审核待办（moderation_status=PENDING/REJECTED 且审核管理员）：展示全部待审/驳回。
    """
    if moderation_queue_scoped and viewer.can_see_all_skill_moderation_states:
        return None
    pt = func.lower(func.coalesce(MarketAssetDB.plugin_type, ""))
    is_skill_like = pt.in_(_SKILL_LIKE_PLUGIN_TYPES_LIST)
    has_publish_result = and_(
        MarketAssetDB.publish_result.isnot(None),
        MarketAssetDB.publish_result != "",
    )
    public_version_exists = and_(
        MarketAssetDB.public_latest_version.isnot(None),
        MarketAssetDB.public_latest_version != "",
    )
    new_skill_model = and_(is_skill_like, or_(has_publish_result, public_version_exists))
    # 主表 moderation_status 空值历史上视为已通过；但若仅有显式 PENDING（无任一 APPROVED）且主表未回填，应对外隐藏。
    # 「旧版已通过 + 新版本待审」时须保持公开展示（与聚合 any_approved 一致）。
    pend_ver_explicit = _version_pending_moderation_exists()
    appr_ver_explicit = exists(
        select(1)
        .select_from(MarketAssetVersionDB)
        .where(
            MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
            MarketAssetVersionDB.moderation_status == MODERATION_APPROVED,
        )
        .correlate(MarketAssetDB)
    )
    main_null_or_empty = or_(
        MarketAssetDB.moderation_status.is_(None),
        MarketAssetDB.moderation_status == "",
    )
    legacy_skill_ok = or_(
        and_(main_null_or_empty, ~pend_ver_explicit),
        and_(main_null_or_empty, pend_ver_explicit, appr_ver_explicit),
    )
    skill_public_ok = or_(MarketAssetDB.moderation_status == MODERATION_APPROVED, legacy_skill_ok)
    skill_market_asset_ok = or_(
        MarketAssetDB.moderation_status.is_(None),
        MarketAssetDB.moderation_status == "",
        MarketAssetDB.moderation_status == MODERATION_APPROVED,
    )
    skill_public_surface = or_(
        and_(new_skill_model, public_version_exists),
        and_(not_(new_skill_model), skill_public_ok),
    )
    skill_visibility_public = or_(
        MarketAssetDB.visibility.is_(None),
        MarketAssetDB.visibility == "",
        func.lower(MarketAssetDB.visibility) != "private",
    )
    public_ok = or_(
        not_(is_skill_like),
        and_(skill_visibility_public, skill_market_asset_ok, skill_public_surface),
    )
    uid = (viewer.user_id or "").strip()
    if uid:
        group_grant_exists = exists(
            select(1)
            .select_from(MarketGroupSkillGrantDB)
            .join(MarketGroupMemberDB, MarketGroupMemberDB.group_id == MarketGroupSkillGrantDB.group_id)
            .join(MarketGroupDB, MarketGroupDB.group_id == MarketGroupSkillGrantDB.group_id)
            .where(
                MarketGroupSkillGrantDB.asset_id == MarketAssetDB.asset_id,
                MarketGroupSkillGrantDB.status == "active",
                MarketGroupMemberDB.user_id == uid,
                MarketGroupDB.status == "active",
            )
            .correlate(MarketAssetDB)
        )
        group_ok = and_(is_skill_like, group_grant_exists)
        if publisher_scoped:
            return or_(public_ok, and_(is_skill_like, MarketAssetDB.publisher_id == uid), group_ok)
        # 公开市场（首页/搜索）只展示 public Skill，组群授权的 private Skill 不混入公开列表，
        # 仅通过 /groups/my/skills 等组群视角入口可见
        return public_ok
    return public_ok


def list_icon_version_join_expr(
    viewer: "ViewerContext",
    *,
    publisher_scoped: bool = False,
    moderation_queue_scoped: bool = False,
):
    """列表 icon 联表：公开市场一律用 public_latest_version；仅「我的 Skills」/ 审核待办用 latest。"""
    pt = func.lower(func.coalesce(MarketAssetDB.plugin_type, ""))
    is_skill_like = pt.in_(_SKILL_LIKE_PLUGIN_TYPES_LIST)
    has_publish_result = and_(
        MarketAssetDB.publish_result.isnot(None),
        MarketAssetDB.publish_result != "",
    )
    public_version_exists = and_(
        MarketAssetDB.public_latest_version.isnot(None),
        MarketAssetDB.public_latest_version != "",
    )
    new_skill_model = and_(is_skill_like, or_(has_publish_result, public_version_exists))
    public_icon = case(
        (and_(is_skill_like, public_version_exists), MarketAssetDB.public_latest_version),
        (new_skill_model, None),
        else_=func.coalesce(MarketAssetDB.public_latest_version, MarketAssetDB.latest_version),
    )
    if moderation_queue_scoped and viewer.can_see_all_skill_moderation_states:
        return MarketAssetDB.latest_version
    uid = (viewer.user_id or "").strip()
    if uid and publisher_scoped:
        return case((MarketAssetDB.publisher_id == uid, MarketAssetDB.latest_version), else_=public_icon)
    return public_icon


class MarketAssetRepository(MarketBaseRepository[MarketAssetDB]):
    """Data access for market_assets."""

    def __init__(self, db: Session):
        super().__init__(db, MarketAssetDB)

    def _dialect_supports_json_contains(self) -> bool:
        """JSON_CONTAINS/JSON_TABLE 仅 MySQL 提供；SQLite（单测内存库）走检索路径的 Python 过滤。"""
        return self.db.get_bind().dialect.name in ("mysql", "mariadb")

    def list_tag_options(
        self,
        *,
        plugin_type: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
    ) -> List[Tuple[str, int]]:
        """聚合可见资产的 tags，按使用次数降序返回 (tag, count)。

        MySQL 8 用 JSON_TABLE 展开 + GROUP BY 在库内聚合，仅返回 limit 行；
        不支持的方言（SQLite 单测）返回空。
        可见性与匿名访客的市场列表完全同口径（skill_moderation_list_clause +
        OFFLINE 排除），保证 chip 上的 count 与点击后的列表结果一致。
        keyword 非空时按子串（ilike %kw%）在 GROUP BY 前过滤，使低频长尾标签
        也能被搜到；limit 仍作用于匹配后集合，按使用次数降序。kw 先做 NFKC 归一
        化（全角->半角，与发布校验同口径）再转义 %/_ 通配符（escape='\\'，令其
        按字面量匹配），见 _prepare_tag_keyword_for_like。
        """
        if not self._dialect_supports_json_contains():
            return []
        # JSON_TABLE 展开 tags（varchar(255)：发布限制单标签 <=64 字符，宽裕覆盖；
        # 超限标签得 NULL 行，isnot(None) 剔除，避免混入计数键）。
        # JOIN ... ON 1 是 MySQL JSON_TABLE 的标准行转列写法（逗号连表会触发笛卡尔积告警）
        tags_table = func.json_table(
            MarketAssetDB.tags,
            literal_column("'$[*]' COLUMNS (tag VARCHAR(255) PATH '$')"),
        ).table_valued("tag")
        expanded = (
            select(tags_table.c.tag, MarketAssetDB.asset_id.label("asset_id"))
            .select_from(MarketAssetDB.__table__.join(tags_table, literal_column("1")))
            .where(MarketAssetDB.status != "OFFLINE")
            .where(MarketAssetDB.tags.isnot(None))
            .where(tags_table.c.tag.isnot(None))
        )
        pt = (plugin_type or "").strip()
        if pt:
            expanded = expanded.where(MarketAssetDB.plugin_type == pt)
        # 子串过滤须在 GROUP BY 前：否则 count 预排序 + limit 会先把低频长尾标签剔除，搜不到。
        # kw 与发布校验/parse_tag_filter 同口径 NFKC 归一 + 转义 LIKE 通配符（_/%），
        # escape='\\' 令其按字面量匹配；详见 _prepare_tag_keyword_for_like。
        kw_escaped = _prepare_tag_keyword_for_like(keyword)
        if kw_escaped:
            expanded = expanded.where(tags_table.c.tag.ilike(f"%{kw_escaped}%", escape="\\"))
        expanded = expanded.subquery()
        # 可见资产子查询：与市场列表同口径（匿名访客），IN 半连接
        visible_assets = select(MarketAssetDB.asset_id)
        mod_clause = skill_moderation_list_clause(_anonymous_viewer())
        if mod_clause is not None:
            visible_assets = visible_assets.where(mod_clause)
        visible_assets = visible_assets.subquery()
        q = (
            select(expanded.c.tag, func.count(func.distinct(expanded.c.asset_id)).label("cnt"))
            .where(expanded.c.asset_id.in_(select(visible_assets.c.asset_id)))
            .group_by(expanded.c.tag)
            .order_by(desc("cnt"), expanded.c.tag.asc())
            .limit(limit)
        )
        rows = self.db.execute(q).fetchall()
        return [(r.tag, int(r.cnt)) for r in rows]

    @staticmethod
    def _is_publisher_scoped_list(params: PluginListQuery, viewer: "ViewerContext") -> bool:
        """个人「我的 Skills」：publisher_id 筛选且为当前用户本人。"""
        pid = (params.publisher_id or "").strip()
        uid = (viewer.user_id or "").strip()
        return bool(pid and uid and pid == uid)

    @staticmethod
    def _is_direct_asset_access(params: PluginListQuery, viewer: "ViewerContext") -> bool:
        """按 asset_id 直接访问详情：已登录用户应能看到自己发布或经组群授权的私有 Skill。"""
        aid = (params.asset_id or "").strip()
        uid = (viewer.user_id or "").strip()
        return bool(aid and uid)

    @staticmethod
    def _is_moderation_queue_scoped_list(params: PluginListQuery, viewer: "ViewerContext") -> bool:
        """审核待办：显式 moderation_status=PENDING/REJECTED 且为审核管理员。"""
        if not viewer.can_see_all_skill_moderation_states:
            return False
        ms = (params.moderation_status or "").strip().upper()
        return ms in (MODERATION_PENDING, MODERATION_REJECTED)

    def is_market_public_scoped_list(self, params: PluginListQuery, viewer: "ViewerContext") -> bool:
        """首页/搜索等公开市场列表：非「我的 Skills」、非直接访问、非审核待办。"""
        return not (
            self._is_publisher_scoped_list(params, viewer)
            or self._is_direct_asset_access(params, viewer)
            or self._is_moderation_queue_scoped_list(params, viewer)
        )

    def create_asset(self, params: AssetCreate) -> MarketAssetDB:
        now_ms = int(time.time() * 1000)
        obj_in = params.model_dump()
        obj_in.update({"create_time": now_ms, "update_time": now_ms})
        return self.create(obj_in)

    def update_latest_version(self, asset: MarketAssetDB, version: str) -> MarketAssetDB:
        now_ms = int(time.time() * 1000)
        return self.update(
            asset,
            {
                "latest_version": version,
                "update_time": now_ms,
            },
        )

    def get_by_asset_id(self, asset_id: str) -> Optional[MarketAssetDB]:
        return self.filter_by(asset_id=asset_id).first()

    def list_by_publisher_name_and_type(
        self,
        publisher_id: str,
        name: str,
        asset_type: str = "plugin",
    ) -> List[MarketAssetDB]:
        """All assets for same publisher + exact name + type (0/1/many for publish resolution)."""
        return self.filter_by(
            publisher_id=publisher_id,
            name=name,
            asset_type=asset_type,
        ).all()

    def list_by_publisher_name_type_and_plugin_type(
        self,
        publisher_id: str,
        name: str,
        asset_type: str = "plugin",
        plugin_type: str | None = None,
    ) -> List[MarketAssetDB]:
        q = self.query().filter(
            MarketAssetDB.publisher_id == publisher_id,
            MarketAssetDB.name == name,
            MarketAssetDB.asset_type == asset_type,
        )
        if plugin_type is None:
            q = q.filter(MarketAssetDB.plugin_type.is_(None))
        else:
            q = q.filter(MarketAssetDB.plugin_type == plugin_type)
        return q.all()

    def list_by_publisher(
        self,
        publisher_id: str,
        limit: int = 50,
    ) -> List[MarketAssetDB]:
        return (
            self.filter_by(publisher_id=publisher_id)
            .order_by(MarketAssetDB.create_time.desc())
            .limit(limit)
            .all()
        )

    def count_skills_by_publisher(self, publisher_id: str) -> int:
        """Count published skill-like assets by publisher (excludes OFFLINE status)."""
        return (
            self.query()
            .filter(
                MarketAssetDB.publisher_id == publisher_id,
                MarketAssetDB.plugin_type.in_(_SKILL_LIKE_PLUGIN_TYPES_LIST),
                MarketAssetDB.status != "OFFLINE",
            )
            .count()
        )

    def get_by_external_id(self, external_id: str) -> MarketAssetDB | None:
        eid = (external_id or "").strip()
        if not eid:
            return None
        return self.query().filter(MarketAssetDB.external_id == eid).first()

    def search_by_name(
        self,
        keyword: str,
        limit: int = 50,
    ) -> List[MarketAssetDB]:
        return (
            self.query()
            .filter(MarketAssetDB.name.ilike(f"%{keyword}%"))
            .order_by(MarketAssetDB.view_count.desc())
            .limit(limit)
            .all()
        )

    def search_grantable_skills_for_publisher(
        self,
        *,
        publisher_id: str,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[List[MarketAssetDB], int]:
        q = self.query().filter(
            MarketAssetDB.publisher_id == publisher_id,
            MarketAssetDB.status != "OFFLINE",
            MarketAssetDB.plugin_type.in_(_SKILL_LIKE_PLUGIN_TYPES_LIST),
            func.lower(func.coalesce(MarketAssetDB.visibility, "public")) == "private",
        )
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            q = q.filter(
                or_(
                    MarketAssetDB.asset_id.ilike(kw),
                    MarketAssetDB.name.ilike(kw),
                    MarketAssetDB.display_name.ilike(kw),
                    MarketAssetDB.short_desc.ilike(kw),
                    _tags_text_column().ilike(kw),
                )
            )
        q = q.order_by(MarketAssetDB.update_time.desc(), MarketAssetDB.asset_id.asc())
        total = q.count()
        rows = q.offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def list_plugins(
        self,
        params: PluginListQuery,
        *,
        viewer: "ViewerContext",
    ) -> Tuple[List[Tuple[MarketAssetDB, Optional[str], bool]], int]:
        """
        分页查询插件列表，默认排除 status=OFFLINE 的资源。
        支持按 asset_id、asset_type、publisher_id、publisher_name（模糊）、
        category_id（精确匹配）、
        plugin_type（精确匹配）、
        search_keyword（对 name/display_name/short_desc/detail_desc 做 OR 模糊）过滤，
        按 order_by 排序。
        返回每行 (asset, file_path, has_icon)。
        """
        q_assets = self.query().filter(MarketAssetDB.status != "OFFLINE")

        if params.asset_id:
            q_assets = q_assets.filter(MarketAssetDB.asset_id == params.asset_id)
        if params.asset_type:
            q_assets = q_assets.filter(MarketAssetDB.asset_type == params.asset_type)
        if params.publisher_id:
            q_assets = q_assets.filter(MarketAssetDB.publisher_id == params.publisher_id)
        if params.publisher_name and params.publisher_name.strip():
            q_assets = q_assets.filter(
                MarketAssetDB.publisher_name.ilike(f"%{params.publisher_name.strip()}%")
            )
        if params.category_id and params.category_id.strip():
            q_assets = q_assets.filter(MarketAssetDB.category_id == params.category_id.strip())
        if params.plugin_type and params.plugin_type.strip():
            pt_raw = params.plugin_type.strip()
            pt_list = [p.strip() for p in pt_raw.split(",") if p.strip()]
            if len(pt_list) == 1:
                q_assets = q_assets.filter(MarketAssetDB.plugin_type == pt_list[0])
            elif len(pt_list) > 1:
                q_assets = q_assets.filter(MarketAssetDB.plugin_type.in_(pt_list))
        if params.plugin_type_exclude and params.plugin_type_exclude.strip():
            ex = params.plugin_type_exclude.strip()
            if is_skill_like_plugin_type(ex):
                q_assets = q_assets.filter(
                    or_(
                        MarketAssetDB.plugin_type.is_(None),
                        not_(MarketAssetDB.plugin_type.in_(_SKILL_LIKE_PLUGIN_TYPES_LIST)),
                    )
                )
            else:
                q_assets = q_assets.filter(
                    or_(
                        MarketAssetDB.plugin_type.is_(None),
                        MarketAssetDB.plugin_type != ex,
                    )
                )
        if params.search_keyword and params.search_keyword.strip():
            kw = f"%{params.search_keyword.strip()}%"
            # 关键词搜索命中标签文本（tags JSON 列归一化投影）
            q_assets = q_assets.filter(
                or_(
                    MarketAssetDB.name.ilike(kw),
                    MarketAssetDB.display_name.ilike(kw),
                    MarketAssetDB.short_desc.ilike(kw),
                    MarketAssetDB.detail_desc.ilike(kw),
                    _tags_text_column().ilike(kw),
                )
            )

        # 标签精确过滤（tags 参数）：JSON_CONTAINS 仅 MySQL 支持；
        # SQLite（单测内存库）无此函数，跳过——由检索路径的 Python 集合过滤兜住。
        tag_filter = parse_tag_filter(params.tags)
        if tag_filter and self._dialect_supports_json_contains():
            json_contains = [
                func.json_contains(MarketAssetDB.tags, json.dumps(t)) for t in tag_filter
            ]
            if params.tags_match == "any":
                q_assets = q_assets.filter(or_(*json_contains))
            else:
                q_assets = q_assets.filter(*json_contains)

        publisher_scoped = self._is_publisher_scoped_list(params, viewer) or self._is_direct_asset_access(
            params, viewer
        )
        moderation_queue_scoped = self._is_moderation_queue_scoped_list(params, viewer)

        mod_clause = skill_moderation_list_clause(
            viewer,
            publisher_scoped=publisher_scoped,
            moderation_queue_scoped=moderation_queue_scoped,
        )
        if mod_clause is not None:
            q_assets = q_assets.filter(mod_clause)

        ms = (params.moderation_status or "").strip().upper() if params.moderation_status else ""
        if ms == "PENDING":
            # Skill-like：系统审查通过后等待人工审核，或旧数据显式 PENDING，均视为待审。
            pt = (params.plugin_type or "").strip().lower()
            pt_parts = {p.strip() for p in pt.split(",") if p.strip()} if pt else set()
            if not pt_parts or pt_parts & SKILL_LIKE_PLUGIN_TYPES:
                q_assets = q_assets.filter(pending_moderation_version_exists_for_asset())
            else:
                q_assets = q_assets.filter(MarketAssetDB.moderation_status == "PENDING")
        elif ms == "REJECTED":
            q_assets = q_assets.filter(MarketAssetDB.moderation_status == "REJECTED")
        elif ms == "APPROVED":
            q_assets = q_assets.filter(
                or_(
                    MarketAssetDB.moderation_status.is_(None),
                    MarketAssetDB.moderation_status == "",
                    MarketAssetDB.moderation_status == "APPROVED",
                )
            )

        total = q_assets.count()

        order_col = getattr(
            MarketAssetDB,
            params.order_by if hasattr(MarketAssetDB, params.order_by) else "install_count",
        )
        # 置顶：pin_order 非 NULL 的排在前，且 pin_order 升序；其余按原 order_by 规则
        pin_group = case((MarketAssetDB.pin_order.is_(None), 1), else_=0)
        q_assets = q_assets.order_by(
            asc(pin_group),
            asc(MarketAssetDB.pin_order),
            desc(order_col) if params.desc else asc(order_col),
            asc(MarketAssetDB.asset_id),
        )

        page = max(1, params.page)
        page_size = max(1, min(params.page_size, 200))
        offset = (page - 1) * page_size
        q = (
            q_assets.outerjoin(
                MarketAssetVersionDB,
                and_(
                    MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                    MarketAssetVersionDB.version == list_icon_version_join_expr(
                        viewer,
                        publisher_scoped=publisher_scoped,
                        moderation_queue_scoped=moderation_queue_scoped,
                    ),
                ),
            )
            .add_columns(MarketAssetVersionDB.file_path, MarketAssetVersionDB.has_icon)
        )
        rows: List[Tuple[MarketAssetDB, Optional[str], bool]] = q.offset(offset).limit(page_size).all()

        return rows, total

    def get_assets_with_file_paths(
        self,
        asset_ids: List[str],
        *,
        viewer: "ViewerContext",
    ) -> List[Tuple[MarketAssetDB, Optional[str], bool]]:
        """Batch-fetch assets by asset_id list + their latest-version file_path and has_icon.

        Excludes OFFLINE assets. Result order is database-defined; caller must
        re-sort by the original asset_ids sequence to preserve retrieval ranking.
        """
        if not asset_ids:
            return []
        q = (
            self.query()
            .filter(
                MarketAssetDB.asset_id.in_(asset_ids),
                MarketAssetDB.status != "OFFLINE",
            )
        )
        mod_clause = skill_moderation_list_clause(viewer)
        if mod_clause is not None:
            q = q.filter(mod_clause)
        return (
            q.outerjoin(
                MarketAssetVersionDB,
                and_(
                    MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                    MarketAssetVersionDB.version == list_icon_version_join_expr(viewer),
                ),
            )
            .add_columns(MarketAssetVersionDB.file_path, MarketAssetVersionDB.has_icon)
            .all()
        )

    def delete_asset(self, asset_id: str) -> int:
        """Delete asset by asset_id. Caller owns transaction commit/rollback."""
        return self.query().filter(MarketAssetDB.asset_id == asset_id).delete(synchronize_session=False)

    def increase_install_count_atomic(self, asset_id: str, now_ms: Optional[int] = None) -> int:
        """原子自增 install_count，返回受影响行数（绝不改写 update_time；now_ms 仅为兼容签名保留）。"""
        # update_time 表示资产内容的“最近更新时间”，仅访问下载元数据不应推后它，否则破坏按更新时间排序。
        return (
            self.query()
            .filter(MarketAssetDB.asset_id == asset_id)
            .update(
                {
                    MarketAssetDB.install_count: MarketAssetDB.install_count + 1,
                },
                synchronize_session=False,
            )
        )

    def get_counts_batch(self, asset_ids: List[str]) -> Dict[str, Dict[str, int]]:
        """批量查 like_count / star_count，返回 {asset_id: {like_count, star_count}}。"""
        if not asset_ids:
            return {}
        rows = (
            self.db.query(
                MarketAssetDB.asset_id,
                MarketAssetDB.like_count,
                MarketAssetDB.star_count,
            )
            .filter(MarketAssetDB.asset_id.in_(asset_ids))
            .all()
        )
        return {
            r.asset_id: {
                "like_count": int(r.like_count or 0),
                "star_count": int(r.star_count or 0),
            }
            for r in rows
        }

    def filter_visible_asset_ids(self, asset_ids: List[str], viewer: "ViewerContext") -> set:
        """返回 asset_ids 中对 viewer 可见的子集：排除 OFFLINE 并应用统一 Skill ACL。"""
        if not asset_ids:
            return set()
        q = self.query().filter(
            MarketAssetDB.asset_id.in_(asset_ids),
            MarketAssetDB.status != "OFFLINE",
        )
        mod_clause = skill_moderation_list_clause(
            viewer, publisher_scoped=True, moderation_queue_scoped=True
        )
        if mod_clause is not None:
            q = q.filter(mod_clause)
        return {row.asset_id for row in q.with_entities(MarketAssetDB.asset_id).all()}

    def lock_for_update(self, asset_id: str) -> Optional[MarketAssetDB]:
        """SELECT ... FOR UPDATE：锁定资产行用于 like 计数的强一致更新。"""
        return (
            self.query()
            .filter(MarketAssetDB.asset_id == asset_id)
            .with_for_update()
            .first()
        )

    def increase_view_count_atomic(self, asset_id: str) -> int:
        """原子递增 view_count；不修改 update_time，避免影响列表按更新时间排序。"""
        return (
            self.query()
            .filter(MarketAssetDB.asset_id == asset_id)
            .update(
                {MarketAssetDB.view_count: MarketAssetDB.view_count + 1},
                synchronize_session=False,
            )
        )


class MarketAssetVersionRepository(MarketBaseRepository[MarketAssetVersionDB]):
    """Data access for market_asset_versions."""

    def __init__(self, db: Session):
        super().__init__(db, MarketAssetVersionDB)

    def asset_has_explicit_approved_moderation_version(self, asset_id: str) -> bool:
        """是否存在 moderation_status 显式为 APPROVED 的版本行。"""
        row = (
            self.query()
            .filter(
                MarketAssetVersionDB.asset_id == asset_id,
                MarketAssetVersionDB.moderation_status == MODERATION_APPROVED,
            )
            .limit(1)
            .first()
        )
        return row is not None

    def asset_has_explicit_pending_moderation_version(self, asset_id: str) -> bool:
        """是否存在待审版本行（与 pending_moderation_version_filter 一致）。"""
        row = (
            self.query()
            .filter(
                MarketAssetVersionDB.asset_id == asset_id,
                pending_moderation_version_filter(),
            )
            .limit(1)
            .first()
        )
        return row is not None

    def list_versions(self, asset_id: str, *, limit: int | None = None) -> List[MarketAssetVersionDB]:
        q = (
            self.filter_by(asset_id=asset_id)
            .order_by(MarketAssetVersionDB.create_time.desc())
        )
        if limit is not None and limit > 0:
            q = q.limit(int(limit))
        return q.all()

    def list_version_strings_by_asset_ids(self, asset_ids: List[str]) -> Dict[str, List[str]]:
        """按 asset_id 聚合版本号字符串；每个资产内按 create_time、version 升序（与发布时间线一致）。"""
        if not asset_ids:
            return {}
        unique_ids = list(dict.fromkeys(asset_ids))
        rows = (
            self.query()
            .filter(MarketAssetVersionDB.asset_id.in_(unique_ids))
            .order_by(
                MarketAssetVersionDB.asset_id.asc(),
                MarketAssetVersionDB.create_time.asc(),
                MarketAssetVersionDB.version.asc(),
            )
            .all()
        )
        out: Dict[str, List[str]] = defaultdict(list)
        for row in rows:
            out[row.asset_id].append(row.version)
        return dict(out)

    def list_all_by_asset_ids(self, asset_ids: List[str]) -> List[MarketAssetVersionDB]:
        """批量拉取多个 asset 下的全部版本行（升序），供列表填充 all_versions / 审核标记。"""
        if not asset_ids:
            return []
        unique_ids = list(dict.fromkeys(asset_ids))
        return (
            self.query()
            .filter(MarketAssetVersionDB.asset_id.in_(unique_ids))
            .order_by(
                MarketAssetVersionDB.asset_id.asc(),
                MarketAssetVersionDB.create_time.asc(),
                MarketAssetVersionDB.version.asc(),
            )
            .all()
        )

    def asset_ids_with_pending_moderation_version(self, asset_ids: List[str]) -> set:
        """存在待审版本的 asset_id 集合（与 pending_moderation_version_filter 一致）。"""
        if not asset_ids:
            return set()
        unique_ids = list(dict.fromkeys(asset_ids))
        rows = (
            self.db.query(MarketAssetVersionDB.asset_id)
            .filter(
                MarketAssetVersionDB.asset_id.in_(unique_ids),
                pending_moderation_version_filter(),
            )
            .distinct()
            .all()
        )
        return {str(r[0]) for r in rows if r[0] is not None}

    def list_versions_chronological(self, asset_id: str) -> List[MarketAssetVersionDB]:
        """Oldest first — used to build cumulative changelog.log from all version rows."""
        return (
            self.filter_by(asset_id=asset_id)
            .order_by(
                MarketAssetVersionDB.create_time.asc(),
                MarketAssetVersionDB.version.asc(),
            )
            .all()
        )

    def get_version(
        self,
        asset_id: str,
        version: str,
    ) -> Optional[MarketAssetVersionDB]:
        return self.filter_by(asset_id=asset_id, version=version).first()

    def lock_version_for_update(
        self,
        asset_id: str,
        version: str,
    ) -> Optional[MarketAssetVersionDB]:
        """锁定版本行，供人工审核等 read-check-write 路径串行化。"""
        return (
            self.query()
            .filter(
                MarketAssetVersionDB.asset_id == asset_id,
                MarketAssetVersionDB.version == version,
            )
            .with_for_update()
            .first()
        )

    def get_latest_version(self, asset_id: str) -> Optional[MarketAssetVersionDB]:
        return (
            self.filter_by(asset_id=asset_id)
            .order_by(MarketAssetVersionDB.create_time.desc())
            .first()
        )

    def count_versions(self, asset_id: str) -> int:
        return self.filter_by(asset_id=asset_id).count()

    def delete_version(self, asset_id: str, version: str) -> int:
        """Delete one version by asset_id and version. Caller owns transaction commit/rollback."""
        return (
            self.query()
            .filter(
                MarketAssetVersionDB.asset_id == asset_id,
                MarketAssetVersionDB.version == version,
            )
            .delete(synchronize_session=False)
        )

    def delete_all_versions(self, asset_id: str) -> int:
        """Delete all versions of an asset. Caller owns transaction commit/rollback."""
        return self.query().filter(MarketAssetVersionDB.asset_id == asset_id).delete(synchronize_session=False)

    def create_version(self, params: AssetVersionCreate) -> MarketAssetVersionDB:
        now_ms = int(time.time() * 1000)
        obj_in = params.model_dump()
        obj_in["create_time"] = now_ms
        return self.create(obj_in)


class PluginFetchRecordRepository(MarketBaseRepository[PluginFetchRecordDB]):
    """Data access for plugin_fetch_records."""

    def __init__(self, db: Session):
        super().__init__(db, PluginFetchRecordDB)

    def create_fetch_record(
        self,
        *,
        asset_id: str,
        version_id: str,
        fetch_user_id: Optional[str] = None,
        create_time: Optional[int] = None,
    ) -> PluginFetchRecordDB:
        now_ms = create_time if create_time is not None else int(time.time() * 1000)
        row = PluginFetchRecordDB(
            asset_id=asset_id,
            version_id=version_id,
            fetch_user_id=fetch_user_id,
            create_time=now_ms,
        )
        self.db.add(row)
        return row


class MarketSkillReviewRepository(MarketBaseRepository[MarketSkillReviewDB]):
    """Data access for market_skill_reviews."""

    def __init__(self, db: Session):
        super().__init__(db, MarketSkillReviewDB)

    def delete_by_asset_id(self, asset_id: str) -> int:
        """Delete review rows by asset. Caller owns transaction commit/rollback."""
        return (
            self.query()
            .filter(MarketSkillReviewDB.asset_id == asset_id)
            .delete(synchronize_session=False)
        )

    def delete_by_version_id(self, version_id: str) -> int:
        """Delete review rows by version. Caller owns transaction commit/rollback."""
        return (
            self.query()
            .filter(MarketSkillReviewDB.version_id == version_id)
            .delete(synchronize_session=False)
        )


class MarketAssetInteractionRepository(MarketBaseRepository[MarketAssetInteractionDB]):
    """Data access for market_asset_interactions (like / star)."""

    def __init__(self, db: Session):
        super().__init__(db, MarketAssetInteractionDB)

    def get_interaction(
        self, asset_id: str, user_id: str, action_type: str
    ) -> Optional[MarketAssetInteractionDB]:
        return (
            self.query()
            .filter(
                MarketAssetInteractionDB.asset_id == asset_id,
                MarketAssetInteractionDB.user_id == user_id,
                MarketAssetInteractionDB.action_type == action_type,
            )
            .first()
        )

    def get_user_interactions(
        self, asset_id: str, user_id: str
    ) -> List[MarketAssetInteractionDB]:
        return (
            self.query()
            .filter(
                MarketAssetInteractionDB.asset_id == asset_id,
                MarketAssetInteractionDB.user_id == user_id,
            )
            .all()
        )

    def add_interaction(
        self, asset_id: str, user_id: str, action_type: str
    ) -> MarketAssetInteractionDB:
        """db.add() 不 commit，由调用方统一提交。"""
        now_ms = int(time.time() * 1000)
        row = MarketAssetInteractionDB(
            asset_id=asset_id,
            user_id=user_id,
            action_type=action_type,
            create_time=now_ms,
            update_time=now_ms,
        )
        self.db.add(row)
        return row

    def remove_interaction(
        self, asset_id: str, user_id: str, action_type: str
    ) -> int:
        """delete 不 commit，由调用方统一提交。返回受影响行数。"""
        return (
            self.query()
            .filter(
                MarketAssetInteractionDB.asset_id == asset_id,
                MarketAssetInteractionDB.user_id == user_id,
                MarketAssetInteractionDB.action_type == action_type,
            )
            .delete(synchronize_session=False)
        )

    def get_user_interactions_batch(
        self, asset_ids: List[str], user_id: str
    ) -> set:
        """批量查用户对多个资产的交互，返回 {(asset_id, action_type)} 集合。"""
        if not asset_ids or not user_id:
            return set()
        rows = (
            self.db.query(
                MarketAssetInteractionDB.asset_id,
                MarketAssetInteractionDB.action_type,
            )
            .filter(
                MarketAssetInteractionDB.asset_id.in_(asset_ids),
                MarketAssetInteractionDB.user_id == user_id,
            )
            .all()
        )
        return {(r.asset_id, r.action_type) for r in rows}

    def list_assets_by_user_action(
        self,
        user_id: str,
        action_type: str,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[MarketAssetDB], int]:
        """返回用户点赞/收藏的资产列表（联表 market_assets），按交互时间降序。"""
        page = max(1, page)
        page_size = max(1, min(page_size, 100))

        q = (
            self.db.query(MarketAssetDB)
            .join(
                MarketAssetInteractionDB,
                and_(
                    MarketAssetInteractionDB.asset_id.collate("utf8mb4_unicode_ci") == MarketAssetDB.asset_id,
                    MarketAssetInteractionDB.user_id == user_id,
                    MarketAssetInteractionDB.action_type == action_type,
                ),
            )
            .filter(MarketAssetDB.status != "OFFLINE")
            .order_by(MarketAssetInteractionDB.create_time.desc())
        )
        # 我的点赞/收藏：skill-like 仅展示审核通过；其它类型资产不受影响。
        pt = func.lower(func.coalesce(MarketAssetDB.plugin_type, ""))
        is_skill_like = pt.in_(_SKILL_LIKE_PLUGIN_TYPES_LIST)
        has_publish_result = and_(
            MarketAssetDB.publish_result.isnot(None),
            MarketAssetDB.publish_result != "",
        )
        public_version_exists = and_(
            MarketAssetDB.public_latest_version.isnot(None),
            MarketAssetDB.public_latest_version != "",
        )
        new_skill_model = and_(is_skill_like, or_(has_publish_result, public_version_exists))
        pend_ver_explicit = exists(
            select(1)
            .select_from(MarketAssetVersionDB)
            .where(
                MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                MarketAssetVersionDB.moderation_status == MODERATION_PENDING,
            )
            .correlate(MarketAssetDB)
        )
        appr_ver_explicit = exists(
            select(1)
            .select_from(MarketAssetVersionDB)
            .where(
                MarketAssetVersionDB.asset_id == MarketAssetDB.asset_id,
                MarketAssetVersionDB.moderation_status == MODERATION_APPROVED,
            )
            .correlate(MarketAssetDB)
        )
        main_null_or_empty = or_(
            MarketAssetDB.moderation_status.is_(None),
            MarketAssetDB.moderation_status == "",
        )
        legacy_skill_ok = or_(
            and_(main_null_or_empty, ~pend_ver_explicit),
            and_(main_null_or_empty, pend_ver_explicit, appr_ver_explicit),
            MarketAssetDB.moderation_status == MODERATION_APPROVED,
        )
        q = q.filter(
            or_(
                not_(pt.in_(_SKILL_LIKE_PLUGIN_TYPES_LIST)),
                and_(new_skill_model, public_version_exists),
                and_(is_skill_like, not_(new_skill_model), legacy_skill_ok),
            )
        )

        total = q.count()
        items = q.offset((page - 1) * page_size).limit(page_size).all()
        return items, total

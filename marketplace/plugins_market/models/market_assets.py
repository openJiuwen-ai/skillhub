# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Integer,
    String,
    Text,
    JSON,
    Numeric,
    Index,
    UniqueConstraint,
    ForeignKey,
)

from .base import Base


class MarketAssetDB(Base):
    __tablename__ = "market_assets"

    asset_id = Column(String(64), primary_key=True, nullable=False)
    asset_type = Column(String(32), nullable=False)
    name = Column(String(128), nullable=False)
    display_name = Column(String(128), nullable=False)
    short_desc = Column(String(4096), nullable=True)
    detail_desc = Column(Text, nullable=True)
    publisher_id = Column(String(64), nullable=False)
    publisher_name = Column(String(128), nullable=False)
    tags = Column(JSON, nullable=True)
    # Offline classifier output for skill assets.
    category_id = Column(String(64), nullable=True)
    category_name = Column(String(128), nullable=True)
    status = Column(String(32), nullable=True, default="PUBLISHED")
    # Skill 聚合审核：由 market_asset_versions 重算。任一审通过则 APPROVED；无通过但有待审则 PENDING；全驳回则 REJECTED
    moderation_status = Column(String(32), nullable=True)
    moderation_reject_reason = Column(Text, nullable=True)
    # Skill 统一发布结果：reviewing | pending_moderation | publish_success | publish_failed
    publish_result = Column(String(32), nullable=True)
    # 资产市场可见性：public 进入公开市场；private 仅作者/管理员/组授权成员可见
    visibility = Column(String(32), nullable=False, default="public")
    # 对外展示/下载/索引使用的最新「已通过审」版本号；无通过版本时为 NULL
    public_latest_version = Column(String(32), nullable=True)
    certification = Column(String(32), nullable=True)
    plugin_type = Column(String(32), nullable=True)
    latest_version = Column(String(32), nullable=True)
    view_count = Column(Integer, nullable=False, default=0)
    install_count = Column(Integer, nullable=False, default=0)
    like_count = Column(Integer, nullable=False, default=0)
    star_count = Column(Integer, nullable=False, default=0)
    create_time = Column(BigInteger, nullable=True)
    update_time = Column(BigInteger, nullable=True)
    review_count = Column(Integer, nullable=False, default=0)
    average_rating = Column(Numeric(3, 2), nullable=False, default=8.00)
    # 置顶顺序：NULL 表示不置顶；手动填 1、2、3… 数字越小越靠前
    pin_order = Column(Integer, nullable=True)
    # 火爆值：近期下载+累计下载+浏览+互动+评分的加权对数综合分（离线定时重算，用于"热门"排序）
    hot_score = Column(Float, nullable=False, default=0.0)

    # Git 接入：见 sql/incremental/v0.0.2.B001/openjiuwen_market/DDL/market_assets.sql
    storage_mode = Column(String(32), nullable=True)
    external_id = Column(String(128), nullable=True)
    git_source_id = Column(String(64), nullable=True)
    git_visibility = Column(String(32), nullable=True)
    resolved_commit_sha = Column(String(40), nullable=True)
    declared_skill_version = Column(String(64), nullable=True)
    artifact_content_key = Column(String(64), nullable=True)
    # 最近一次 Git 同步成功后的条目内容 SHA-256（hex）；兼容旧数据中的 zip 字节摘要
    git_sync_payload_sha256 = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "publisher_id",
            "asset_type",
            "name",
            name="uk_publisher_asset_type_name",
        ),
        Index("idx_asset_type", asset_type),
        Index("idx_name", name),
        Index("idx_publisher_id", publisher_id),
        Index("idx_status", status),
        Index("idx_certification", certification),
        Index("idx_install_count", install_count),
        Index("idx_like_count", like_count),
        Index("idx_star_count", star_count),
        Index("idx_category_id", category_id),
        Index("idx_pin_order", pin_order),
        Index("idx_hot_score", hot_score),
        Index("idx_moderation_status", moderation_status),
        Index("idx_publish_result", publish_result),
        Index("idx_market_assets_visibility", visibility),
    )


class MarketAssetVersionDB(Base):
    __tablename__ = "market_asset_versions"

    version_id = Column(String(64), primary_key=True, nullable=False)
    asset_id = Column(
        String(64),
        ForeignKey("market_assets.asset_id"),
        nullable=False,
        index=True,
    )
    version = Column(String(32), nullable=False)
    changelog = Column(Text, nullable=True)
    status = Column(String(32), nullable=True, default="ACTIVE")
    create_time = Column(BigInteger, nullable=True)
    file_path = Column(String(512), nullable=True)
    artifact_sha256 = Column(String(64), nullable=True)
    has_icon = Column(Boolean, nullable=False, default=False)
    # 版本级审核：PENDING | APPROVED | REJECTED；NULL 按已通过（与历史库兼容）
    moderation_status = Column(String(32), nullable=True)
    moderation_reject_reason = Column(Text, nullable=True)
    # 版本级统一发布结果：reviewing | pending_moderation | publish_success | publish_failed
    publish_result = Column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("asset_id", "version", name="uk_asset_version"),
    )


class PluginFetchRecordDB(Base):
    __tablename__ = "plugin_fetch_records"

    id = Column(BigInteger, primary_key=True, autoincrement=True, nullable=False)
    asset_id = Column(String(64), nullable=False)
    version_id = Column(String(64), nullable=False)
    fetch_user_id = Column(String(64), nullable=True)
    create_time = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("idx_asset_id", asset_id),
        Index("idx_fetch_user_id", fetch_user_id),
        # 复合索引优化近期下载聚合查询：按 create_time 过滤 + asset_id 分组
        Index("idx_create_time_asset_id", create_time, asset_id),
    )


class MarketSkillReviewDB(Base):
    __tablename__ = "market_skill_reviews"

    id = Column(BigInteger, primary_key=True, autoincrement=True, nullable=False)
    asset_id = Column(
        String(64),
        ForeignKey("market_assets.asset_id"),
        nullable=False,
        index=True,
    )
    version_id = Column(
        String(64),
        ForeignKey("market_asset_versions.version_id"),
        nullable=False,
        index=True,
    )
    review_status = Column(String(32), nullable=False, default="queued")
    review_failed_reason = Column(Text, nullable=True)
    review_mode = Column(String(64), nullable=True)
    review_engine = Column(String(128), nullable=True)
    model_name = Column(String(256), nullable=True)
    policy_version = Column(String(128), nullable=True)
    score = Column(Integer, nullable=True)
    overall = Column(String(32), nullable=True)
    risk = Column(String(32), nullable=True)
    conclusion = Column(Text, nullable=True)
    metrics_json = Column(JSON, nullable=True)
    sections_json = Column(JSON, nullable=True)
    raw_output_json = Column(JSON, nullable=True)
    trace_id = Column(String(128), nullable=True)
    started_at = Column(BigInteger, nullable=True)
    finished_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("asset_id", "version_id", name="uk_skill_review_asset_version"),
        Index("idx_skill_review_status", review_status),
    )


class MarketAssetInteractionDB(Base):
    __tablename__ = "market_asset_interactions"

    id = Column(BigInteger, primary_key=True, autoincrement=True, nullable=False)
    asset_id = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False)
    action_type = Column(String(32), nullable=False)
    create_time = Column(BigInteger, nullable=True)
    update_time = Column(BigInteger, nullable=True)

    __table_args__ = (
        UniqueConstraint("asset_id", "user_id", "action_type", name="uk_mai_asset_user_action"),
        Index("idx_mai_asset_id", asset_id),
        Index("idx_mai_user_id", user_id),
        Index("idx_mai_action_type", action_type),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

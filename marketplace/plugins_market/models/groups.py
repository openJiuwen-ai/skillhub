# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from .base import Base


class MarketGroupDB(Base):
    __tablename__ = "market_groups"

    group_id = Column(String(64), primary_key=True, nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(String(64), nullable=False)
    owner_name = Column(String(128), nullable=True)
    visibility = Column(String(32), nullable=False, default="private")
    status = Column(String(32), nullable=False, default="active")
    member_count = Column(Integer, nullable=False, default=0)
    skill_count = Column(Integer, nullable=False, default=0)
    create_time = Column(BigInteger, nullable=False)
    update_time = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("idx_market_groups_owner_id", owner_id),
        Index("idx_market_groups_visibility", visibility),
        Index("idx_market_groups_status", status),
        Index("idx_market_groups_update_time", update_time),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )


class MarketGroupMemberDB(Base):
    __tablename__ = "market_group_members"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True, nullable=False)
    group_id = Column(
        String(64),
        ForeignKey("market_groups.group_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(String(64), nullable=False)
    user_name = Column(String(128), nullable=True)
    role = Column(String(32), nullable=False, default="member")
    create_time = Column(BigInteger, nullable=False)
    update_time = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uk_group_member_user"),
        Index("idx_group_members_group_id", group_id),
        Index("idx_group_members_user_id", user_id),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )


class MarketGroupJoinRequestDB(Base):
    __tablename__ = "market_group_join_requests"

    request_id = Column(String(64), primary_key=True, nullable=False)
    group_id = Column(
        String(64),
        ForeignKey("market_groups.group_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(String(64), nullable=False)
    user_name = Column(String(128), nullable=True)
    message = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    operator_id = Column(String(64), nullable=True)
    operator_name = Column(String(128), nullable=True)
    create_time = Column(BigInteger, nullable=False)
    update_time = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", "status", name="uk_group_join_user_status"),
        Index("idx_group_join_group_id", group_id),
        Index("idx_group_join_user_id", user_id),
        Index("idx_group_join_status", status),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )


class MarketGroupSkillGrantDB(Base):
    __tablename__ = "market_group_skill_grants"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True, nullable=False)
    group_id = Column(
        String(64),
        ForeignKey("market_groups.group_id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id = Column(
        String(64),
        ForeignKey("market_assets.asset_id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(String(32), nullable=False, default="pending")
    operator_id = Column(String(64), nullable=True)
    operator_name = Column(String(128), nullable=True)
    create_time = Column(BigInteger, nullable=False)
    update_time = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "asset_id", name="uk_group_skill_grant"),
        Index("idx_group_skill_grants_group_id", group_id),
        Index("idx_group_skill_grants_asset_id", asset_id),
        Index("idx_group_skill_grants_status", status),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

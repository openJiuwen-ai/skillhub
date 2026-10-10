# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from sqlalchemy import BigInteger, Column, Index, Integer, String

from .base import Base


class SiteNotificationDB(Base):
    """站内消息：按 inbox_key 分收件箱，每人每类收件标识最多保留 10 条（由写入时淘汰实现）。"""

    __tablename__ = "site_notifications"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True, nullable=False)
    # l:{login} 与审核管理员配置 MARKET_REVIEW_ADMIN_USERNAMES 对齐；u:{OAuth user id} 与 publisher_id 对齐
    inbox_key = Column(String(192), nullable=False)
    # admin_pending | user_review_done | user_manual_review_approved |
    # user_manual_review_rejected | user_system_review_failed
    template = Column(String(32), nullable=False)
    created_at_ms = Column(BigInteger, nullable=False, index=True)
    read_at_ms = Column(BigInteger, nullable=True)

    __table_args__ = (Index("idx_inbox_created", "inbox_key", "created_at_ms"),)

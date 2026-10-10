# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from sqlalchemy.orm import Session

from plugins_market.models.git_sources import GitSourceDB
from .base_repository import MarketBaseRepository


class GitSourceRepository(MarketBaseRepository[GitSourceDB]):
    """git_sources 表访问。"""

    def __init__(self, db: Session):
        super().__init__(db, GitSourceDB)

    def get_by_id(self, source_id: str) -> GitSourceDB | None:
        sid = (source_id or "").strip()
        if not sid:
            return None
        return self.db.query(GitSourceDB).filter(GitSourceDB.id == sid).first()

    def list_by_user(self, user_id: str) -> list[GitSourceDB]:
        uid = (user_id or "").strip()
        if not uid:
            return []
        return (
            self.db.query(GitSourceDB)
            .filter(GitSourceDB.created_by_user_id == uid)
            .order_by(GitSourceDB.create_time_ms.desc())
            .all()
        )

    def find_global_by_dedup_key(self, dedup_key: str) -> GitSourceDB | None:
        """任意用户是否已用该去重键注册过 Git 源（依赖 git_source_dedup_key 唯一索引）。"""
        dk = (dedup_key or "").strip().lower()
        if not dk:
            return None
        return self.db.query(GitSourceDB).filter(GitSourceDB.git_source_dedup_key == dk).first()

    def count_linked_assets(self, source_id: str) -> int:
        from plugins_market.models.market_assets import MarketAssetDB

        sid = (source_id or "").strip()
        if not sid:
            return 0
        return (
            self.db.query(MarketAssetDB)
            .filter(MarketAssetDB.git_source_id == sid)
            .count()
        )

    def list_linked_asset_ids(self, source_id: str) -> list[str]:
        """返回该 Git 源关联的 skill asset_id 列表（按 create_time 升序）。"""
        from plugins_market.models.market_assets import MarketAssetDB

        sid = (source_id or "").strip()
        if not sid:
            return []
        rows = (
            self.db.query(MarketAssetDB.asset_id)
            .filter(MarketAssetDB.git_source_id == sid)
            .order_by(MarketAssetDB.create_time.asc())
            .all()
        )
        return [str(r[0]).strip() for r in rows if r and r[0]]

    def delete_by_id(self, source_id: str) -> bool:
        row = self.get_by_id(source_id)
        if row is None:
            return False
        self.db.delete(row)
        return True

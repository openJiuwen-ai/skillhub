# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from plugins_market.core.logging import get_logger
from plugins_market.models.playground_usage import PlaygroundUsageDB

logger = get_logger(__name__)


def _today_utc():
    """配额按 UTC 自然日滚动，与 proxy 的 _next_midnight_utc 同一时区，
    避免服务器本地非 UTC 时「显示的重置时间」与「实际计数滚动」错位。"""
    return datetime.now(timezone.utc).date()


class PlaygroundQuotaRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_usage(self, user_id: str) -> int:
        """今日已用 session 数（行不存在时返回 0）。"""
        row = (
            self.db.query(PlaygroundUsageDB)
            .filter(
                PlaygroundUsageDB.user_id == user_id,
                PlaygroundUsageDB.usage_date == _today_utc(),
            )
            .first()
        )
        return row.session_count if row else 0

    def try_increment(self, user_id: str, limit: int) -> tuple[bool, int]:
        """原子性检查并递增今日计数。

        Returns:
            (allowed, used_after_increment)；allowed=False 时 used_after_increment 是当前已用数（未递增）。

        SELECT FOR UPDATE：InnoDB next-key lock 保证并发安全，含同一 (user_id, date)
        首次插入时的竞争。limit <= 0 表示不限制（仅记录，始终返回 True）。
        """
        today = _today_utc()
        now_ms = int(time.time() * 1000)

        row = (
            self.db.query(PlaygroundUsageDB)
            .filter(
                PlaygroundUsageDB.user_id == user_id,
                PlaygroundUsageDB.usage_date == today,
            )
            .with_for_update()
            .first()
        )

        current = row.session_count if row else 0
        if limit > 0 and current >= limit:
            return False, current

        if row is None:
            self.db.add(PlaygroundUsageDB(
                user_id=user_id,
                usage_date=today,
                session_count=1,
                updated_at=now_ms,
            ))
        else:
            row.session_count = current + 1
            row.updated_at = now_ms

        self.db.commit()
        return True, current + 1

    def decrement(self, user_id: str) -> None:
        """退回今日一次计数（floor 0），用于已扣费但请求从未执行的回滚。

        best-effort：行不存在或已为 0 则不动。并发下的轻微偏差可接受（只退不超扣）。
        """
        today = _today_utc()
        row = (
            self.db.query(PlaygroundUsageDB)
            .filter(
                PlaygroundUsageDB.user_id == user_id,
                PlaygroundUsageDB.usage_date == today,
            )
            .with_for_update()
            .first()
        )
        if row is None or row.session_count <= 0:
            return
        row.session_count -= 1
        row.updated_at = int(time.time() * 1000)
        self.db.commit()

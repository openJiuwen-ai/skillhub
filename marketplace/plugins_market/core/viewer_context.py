# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""列表 / 详情 / 下载等接口的访问者上下文（可选登录 + 是否可查看全部 Skill 审核状态）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from plugins_market.core.moderation import (
    MODERATION_REJECTED,
    is_moderated_market_asset_type,
    is_skill_like_plugin_type,
)
from plugins_market.core.publish_result import (
    is_skill_asset_publicly_visible,
    is_skill_version_publicly_visible,
)
from plugins_market.core.review_admins import is_market_moderation_username
from plugins_market.models.market_assets import MarketAssetDB, MarketAssetVersionDB
from plugins_market.repositories.groups_repository import MarketGroupSkillGrantRepository


@dataclass(frozen=True, slots=True)
class ViewerContext:
    """来自 Authorization Bearer / X-System-Token（可选）。"""

    user_id: Optional[str]
    """GitCode 用户 id 或 system_admin_user。"""
    user_login: Optional[str]
    """GitCode login（与发布者展示名一致）；系统管理员 token 时为 system_admin_user。"""
    is_system_admin: bool
    """X-System-Token 校验通过。"""

    @property
    def is_market_moderation_admin(self) -> bool:
        return self.is_system_admin or is_market_moderation_username(self.user_login)

    @property
    def can_see_all_skill_moderation_states(self) -> bool:
        return self.is_market_moderation_admin

    @staticmethod
    def _is_private_skill_asset(asset: MarketAssetDB) -> bool:
        return (getattr(asset, "visibility", None) or "public").strip().lower() == "private"

    def skill_asset_access_source(self, asset: MarketAssetDB, db=None) -> str | None:
        if self.can_see_all_skill_moderation_states:
            return "admin"
        if asset.publisher_id and self.user_id and asset.publisher_id == self.user_id:
            return "owner"
        # 组群授权仅 Skill / SwarmSkill；Agent 三类不走 group ACL。
        if db is not None and self.user_id and is_skill_like_plugin_type(asset.plugin_type):
            grant_repo = MarketGroupSkillGrantRepository(db)
            if grant_repo.user_has_asset_grant(user_id=self.user_id, asset_id=asset.asset_id):
                return "group"
        return None

    def can_view_skill_asset(self, asset: MarketAssetDB, db=None) -> bool:
        acl_source = self.skill_asset_access_source(asset, db)
        if not is_moderated_market_asset_type(asset.plugin_type):
            return not self._is_private_skill_asset(asset) or acl_source in ("admin", "owner")
        if acl_source in ("admin", "owner"):
            return True
        # group 来源（组群授权）仍须满足审核通过：未通过审核的 skill 不可经组群授权绕过审核
        if self._is_private_skill_asset(asset):
            return acl_source == "group" and is_skill_asset_publicly_visible(
                publish_result=getattr(asset, "publish_result", None),
                moderation_status=getattr(asset, "moderation_status", None),
                public_latest_version=getattr(asset, "public_latest_version", None),
            )
        return is_skill_asset_publicly_visible(
            publish_result=getattr(asset, "publish_result", None),
            moderation_status=getattr(asset, "moderation_status", None),
            public_latest_version=getattr(asset, "public_latest_version", None),
        )

    def can_download_skill_asset(self, asset: MarketAssetDB, db=None) -> bool:
        return self.can_view_skill_asset(asset, db)

    def can_see_skill_version_row(self, asset: MarketAssetDB, version_row: MarketAssetVersionDB, db=None) -> bool:
        """非本人、非审核管理员时，仅可查看公开或组群授权的已通过版本；发布者可查看全部自有版本。"""
        acl_source = self.skill_asset_access_source(asset, db)
        if not is_moderated_market_asset_type(asset.plugin_type):
            return not self._is_private_skill_asset(asset) or acl_source in ("admin", "owner")
        if acl_source in ("admin", "owner"):
            return True
        # group 来源（组群授权）仍须满足版本审核通过：不可经组群授权绕过审核查看未通过版本
        if self._is_private_skill_asset(asset) and acl_source != "group":
            return False
        return is_skill_version_publicly_visible(
            asset_publish_result=getattr(asset, "publish_result", None),
            asset_public_latest_version=getattr(asset, "public_latest_version", None),
            version=getattr(version_row, "version", None),
            version_publish_result=getattr(version_row, "publish_result", None),
            version_moderation_status=getattr(version_row, "moderation_status", None),
        )

    def can_download_skill_version_row(self, asset: MarketAssetDB, version_row: MarketAssetVersionDB, db=None) -> bool:
        """下载闸门：驳回版本对任何人都不可下载；其余沿用审核可见性。"""
        acl_source = self.skill_asset_access_source(asset, db)
        if not is_moderated_market_asset_type(asset.plugin_type):
            return not self._is_private_skill_asset(asset) or acl_source in ("admin", "owner")
        version_ms = (getattr(version_row, "moderation_status", None) or "").strip().upper()
        if version_ms == MODERATION_REJECTED:
            return False
        if acl_source in ("admin", "owner"):
            return True
        # group 来源（组群授权）仍须满足版本审核通过：不可经组群授权绕过审核下载未通过版本
        if self._is_private_skill_asset(asset) and acl_source != "group":
            return False
        return is_skill_version_publicly_visible(
            asset_publish_result=getattr(asset, "publish_result", None),
            asset_public_latest_version=getattr(asset, "public_latest_version", None),
            version=getattr(version_row, "version", None),
            version_publish_result=getattr(version_row, "publish_result", None),
            version_moderation_status=getattr(version_row, "moderation_status", None),
        )


# ClawHub / 未登录列表等：仅可访问已审核通过的 Skill
ANONYMOUS_VIEWER = ViewerContext(user_id=None, user_login=None, is_system_admin=False)

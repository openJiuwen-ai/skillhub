# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class SiteNotificationItem(BaseModel):
    model_config = ConfigDict(exclude_none=True)

    id: int
    template: str
    message: str
    created_at_ms: int
    read: bool = Field(description="是否已读")


class SiteNotificationListData(BaseModel):
    model_config = ConfigDict(exclude_none=True)

    items: List[SiteNotificationItem]
    unread_count: int

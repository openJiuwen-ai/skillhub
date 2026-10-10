# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Literal, Optional

from plugins_market.schemas.plugin import PluginListItem

from pydantic import BaseModel, Field, field_validator


GroupMemberRole = Literal["owner", "member"]
GroupVisibility = Literal["private", "listed"]
JoinRequestStatus = Literal["pending", "approved", "rejected"]
GrantStatus = Literal["pending", "active", "rejected", "revoked"]


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=4096)
    visibility: GroupVisibility = "private"

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class GroupUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=4096)
    visibility: Optional[GroupVisibility] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        v = value.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v


class GroupItem(BaseModel):
    group_id: str
    name: str
    description: Optional[str] = None
    owner_id: str
    owner_name: Optional[str] = None
    visibility: GroupVisibility = "private"
    member_count: int = 0
    skill_count: int = 0
    viewer_role: Optional[GroupMemberRole] = None
    viewer_can_manage: bool = False
    join_request_status: Optional[JoinRequestStatus] = None
    create_time: int
    update_time: int


class GroupListQuery(BaseModel):
    keyword: Optional[str] = Field(None, max_length=128)
    role: Optional[str] = Field(None, max_length=32)
    sort: Optional[str] = Field(None, max_length=32)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class GroupSkillListQuery(BaseModel):
    keyword: Optional[str] = Field(None, max_length=128)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class GroupDiscoverQuery(BaseModel):
    keyword: Optional[str] = Field(None, max_length=128)
    filter_by: Optional[str] = Field(None, max_length=32)
    sort: Optional[str] = Field(None, max_length=32)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class GroupGrantableSkillQuery(BaseModel):
    keyword: Optional[str] = Field(None, max_length=128)
    group_id: Optional[str] = Field(None, max_length=64)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class GroupStatusListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    status: Optional[str] = None


class GroupListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[GroupItem]


class GroupMemberItem(BaseModel):
    user_id: str
    user_name: Optional[str] = None
    role: GroupMemberRole
    create_time: int
    update_time: int


class GroupMemberListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[GroupMemberItem]


class GroupMemberUpsertRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    user_name: Optional[str] = Field(None, max_length=128)
    role: Literal["member"] = "member"

    @field_validator("user_id")
    @classmethod
    def normalize_user_id(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("user_id must not be empty")
        return v


class GroupJoinRequestCreate(BaseModel):
    message: Optional[str] = Field(None, max_length=4096)


class GroupJoinRequestItem(BaseModel):
    request_id: str
    group_id: str
    user_id: str
    user_name: Optional[str] = None
    message: Optional[str] = None
    status: JoinRequestStatus
    create_time: int
    update_time: int


class GroupJoinRequestListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[GroupJoinRequestItem]


class GroupJoinRequestDecision(BaseModel):
    status: Literal["approved", "rejected"]




class GroupSkillGrantRequest(BaseModel):
    asset_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("asset_id")
    @classmethod
    def normalize_asset_id(cls, value: str) -> str:
        v = value.strip()
        if not v:
            raise ValueError("asset_id must not be empty")
        return v


class GroupSkillGrantItem(BaseModel):
    group_id: str
    asset_id: str
    skill_name: Optional[str] = None
    skill_display_name: Optional[str] = None
    icon_uri: Optional[str] = None
    latest_version: Optional[str] = None
    public_latest_version: Optional[str] = None
    status: GrantStatus
    viewer_access_source: Optional[Literal["admin", "owner", "group", "public"]] = None
    create_time: int
    update_time: int


class GroupSkillGrantDecision(BaseModel):
    status: Literal["active", "rejected"]


class GroupGrantableSkillItem(BaseModel):
    asset_id: str
    name: str
    display_name: Optional[str] = None
    short_desc: Optional[str] = None
    publisher_id: str
    publisher_name: str
    plugin_type: Optional[str] = None
    latest_version: Optional[str] = None
    group_grant_status: Optional[GrantStatus] = None
    grantable: bool = True
    not_grantable_reason: Optional[str] = None


class GroupGrantableSkillListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[GroupGrantableSkillItem]


class GroupSkillGrantListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[GroupSkillGrantItem]


class MyGroupSkillItem(BaseModel):
    group_id: str
    group_name: str
    skill: PluginListItem
    viewer_access_source: Optional[Literal["admin", "owner", "group", "public"]] = None


class MyGroupSkillListResponse(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[MyGroupSkillItem]

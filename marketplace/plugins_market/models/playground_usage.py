# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from sqlalchemy import BigInteger, Column, Date, Integer, String

from .base import Base


class PlaygroundUsageDB(Base):
    __tablename__ = "playground_usage"

    user_id = Column(String(128), primary_key=True, nullable=False)
    usage_date = Column(Date, primary_key=True, nullable=False)
    session_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(BigInteger, nullable=False)

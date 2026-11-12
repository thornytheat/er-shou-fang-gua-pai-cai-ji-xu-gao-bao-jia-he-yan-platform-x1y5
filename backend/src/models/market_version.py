from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class MarketVersion(Base):
    """实体 5：行情版本（可发布、可回溯的行情快照）。

    - draft：待发布草稿，全库最多一条；样本/规则变化后读取时自动失效重算
    - published：发布时冻结主房源、可比样本、异常工单与判价依据，永不改写
    - retracted：被撤回的已发布版本，快照保留供回读与审计
    """

    __tablename__ = "market_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_no: Mapped[str] = mapped_column(String(16), unique=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    flag_counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 快照内容指纹（sha256）：草稿靠它判定「样本或规则已变化，需要重算」
    input_hash: Mapped[str] = mapped_column(String(64))
    # 发布时 CSV 全文的 sha256：回读重新导出可逐字节核对
    csv_hash: Mapped[str] = mapped_column(String(64), default="")
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retracted_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retract_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

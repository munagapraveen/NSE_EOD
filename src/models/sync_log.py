from sqlalchemy import Column, Integer, String, Date, DateTime, Text, UniqueConstraint, Sequence, CheckConstraint
from .base import Base


class SyncLog(Base):
    __tablename__ = "sync_log"
    __table_args__ = (
        UniqueConstraint("sync_type", "sync_date", name="uq_sync_log_type_date"),
        CheckConstraint("status IN ('STARTED', 'SUCCESS', 'FAILED', 'PARTIAL')", name="ck_sync_log_status"),
        CheckConstraint("sync_type IN ('BHAVCOPY', 'INDEX', 'CORPORATE_ACTIONS', 'MARKET_CAP', 'INDICATORS', 'SYMBOL_CHANGES', 'MASTER_DATA', 'FULL_SYNC', 'PARTIAL_SYNC', 'DAILY_SYNC')", name="ck_sync_log_type"),
    )

    id = Column(Integer, Sequence("sync_log_id_seq"), primary_key=True)
    sync_type = Column(String(30), nullable=False)  # BHAVCOPY, INDEX, CORPORATE_ACTIONS, MARKET_CAP, INDICATORS, SYMBOL_CHANGES, MASTER_DATA, FULL_SYNC
    sync_date = Column(Date, nullable=True)  # Trading date being synced (NULL for non-date syncs like master data)
    status = Column(String(20), nullable=False)  # STARTED, SUCCESS, FAILED, PARTIAL
    records_processed = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<SyncLog(type={self.sync_type}, date={self.sync_date}, status={self.status})>"

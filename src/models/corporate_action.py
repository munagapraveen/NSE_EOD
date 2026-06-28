from sqlalchemy import Column, Integer, Date, Numeric, String, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, Sequence, CheckConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .base import Base


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("security_id", "ex_date", "action_type", name="uq_corporate_action_ex_date_type"),
        CheckConstraint("action_type IN ('SPLIT', 'BONUS')", name="ck_corporate_action_type"),
    )

    id = Column(Integer, Sequence("corporate_actions_id_seq"), primary_key=True)
    security_id = Column(Integer, ForeignKey("securities.id"), nullable=False, index=True)
    action_type = Column(String(20), nullable=False)  # SPLIT or BONUS
    ex_date = Column(Date, nullable=False, index=True)
    record_date = Column(Date, nullable=True)
    description = Column(Text, nullable=False)
    
    # Split Details
    old_face_value = Column(Numeric(10, 2), nullable=True)
    new_face_value = Column(Numeric(10, 2), nullable=True)
    
    # Bonus Details
    bonus_ratio_new = Column(Integer, nullable=True)
    bonus_ratio_existing = Column(Integer, nullable=True)
    
    # Calculated
    adjustment_factor = Column(Numeric(12, 6), nullable=False, default=1.0)
    is_processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    security = relationship("Security", back_populates="corporate_actions", primaryjoin="Security.id == CorporateAction.security_id", foreign_keys=[security_id])

    def __repr__(self):
        return f"<CorporateAction(security_id={self.security_id}, type={self.action_type}, ex_date={self.ex_date}, factor={self.adjustment_factor})>"

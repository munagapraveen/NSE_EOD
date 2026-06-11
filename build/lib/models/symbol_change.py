from sqlalchemy import Column, Integer, String, Date, Boolean, DateTime, ForeignKey, UniqueConstraint, Sequence
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .base import Base


class SymbolChange(Base):
    __tablename__ = "symbol_changes"
    __table_args__ = (
        UniqueConstraint("old_symbol", "new_symbol", name="uq_symbol_change_pair"),
    )

    id = Column(Integer, Sequence("symbol_changes_id_seq"), primary_key=True)
    security_id = Column(Integer, nullable=True, index=True)
    old_symbol = Column(String(30), nullable=False, index=True)
    new_symbol = Column(String(30), nullable=False, index=True)
    effective_date = Column(Date, nullable=True)
    is_applied = Column(Boolean, default=False)
    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    # Relationships
    security = relationship("Security", back_populates="symbol_changes", primaryjoin="Security.id == SymbolChange.security_id", foreign_keys=[security_id])

    def __repr__(self):
        return f"<SymbolChange(old={self.old_symbol}, new={self.new_symbol}, applied={self.is_applied})>"

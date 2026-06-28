from sqlalchemy import Column, Integer, Date, String, Boolean, ForeignKey, UniqueConstraint, Sequence
from sqlalchemy.orm import relationship
from .base import Base


class SecurityIndex(Base):
    __tablename__ = "security_indexes"
    __table_args__ = (
        UniqueConstraint("security_id", "quarter_date", "index_name", name="uq_security_index_membership"),
    )

    id = Column(Integer, Sequence("security_indexes_id_seq"), primary_key=True)
    security_id = Column(Integer, ForeignKey("securities.id"), nullable=False, index=True)
    quarter_date = Column(Date, nullable=False, index=True)
    index_name = Column(String(100), nullable=False, index=True)
    is_primary = Column(Boolean, nullable=False, default=False)

    # Relationships
    security = relationship(
        "Security",
        back_populates="security_indexes",
        primaryjoin="Security.id == SecurityIndex.security_id",
        foreign_keys=[security_id]
    )

    def __repr__(self):
        return f"<SecurityIndex(security_id={self.security_id}, quarter={self.quarter_date}, index={self.index_name}, primary={self.is_primary})>"

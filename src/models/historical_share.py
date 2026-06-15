from sqlalchemy import Column, Integer, Date, BigInteger, String, ForeignKey, UniqueConstraint, Sequence
from sqlalchemy.orm import relationship
from .base import Base


class HistoricalShare(Base):
    __tablename__ = "historical_shares"
    __table_args__ = (
        UniqueConstraint("security_id", "quarter_date", name="uq_historical_share_qtr"),
    )

    id = Column(Integer, Sequence("historical_shares_id_seq"), primary_key=True)
    security_id = Column(Integer, ForeignKey("securities.id"), nullable=False, index=True)
    quarter_date = Column(Date, nullable=False, index=True)
    issued_shares = Column(BigInteger, nullable=False)
    source = Column(String(30), nullable=True)

    # Relationships
    security = relationship(
        "Security",
        back_populates="historical_shares",
        primaryjoin="Security.id == HistoricalShare.security_id",
        foreign_keys=[security_id]
    )

    def __repr__(self):
        return f"<HistoricalShare(security_id={self.security_id}, quarter={self.quarter_date}, shares={self.issued_shares})>"

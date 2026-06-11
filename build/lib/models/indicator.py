from sqlalchemy import Column, Integer, Date, Numeric, ForeignKey, UniqueConstraint, Sequence
from sqlalchemy.orm import relationship
from .base import Base


class Indicator(Base):
    __tablename__ = "indicators"
    __table_args__ = (
        UniqueConstraint("security_id", "trade_date", name="uq_indicator_date"),
    )

    id = Column(Integer, Sequence("indicators_id_seq"), primary_key=True)
    security_id = Column(Integer, nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)

    # Simple Moving Averages
    sma_5 = Column(Numeric(12, 2), nullable=True)
    sma_10 = Column(Numeric(12, 2), nullable=True)
    sma_20 = Column(Numeric(12, 2), nullable=True)
    sma_50 = Column(Numeric(12, 2), nullable=True)
    sma_200 = Column(Numeric(12, 2), nullable=True)

    # Relationships
    security = relationship("Security", back_populates="indicators", primaryjoin="Security.id == Indicator.security_id", foreign_keys=[security_id])

    def __repr__(self):
        return f"<Indicator(security_id={self.security_id}, date={self.trade_date})>"

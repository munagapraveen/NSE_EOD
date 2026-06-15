from sqlalchemy import Column, Integer, Date, Numeric, BigInteger, String, ForeignKey, UniqueConstraint, Sequence
from sqlalchemy.orm import relationship
from .base import Base


class MarketCap(Base):
    __tablename__ = "market_cap"
    __table_args__ = (
        UniqueConstraint("security_id", "trade_date", name="uq_market_cap_date"),
    )

    id = Column(Integer, Sequence("market_cap_id_seq"), primary_key=True)
    security_id = Column(Integer, ForeignKey("securities.id"), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    close_price = Column(Numeric(12, 2), nullable=False)
    issued_shares = Column(BigInteger, nullable=False)
    market_cap = Column(Numeric(18, 2), nullable=False)
    shares_source = Column(String(30), nullable=True)

    # Relationships
    security = relationship("Security", back_populates="market_caps", primaryjoin="Security.id == MarketCap.security_id", foreign_keys=[security_id])

    def __repr__(self):
        return f"<MarketCap(security_id={self.security_id}, date={self.trade_date}, market_cap={self.market_cap})>"

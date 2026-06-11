from sqlalchemy import (
    Column, Integer, String, Date, Numeric, BigInteger,
    Boolean, DateTime, ForeignKey, UniqueConstraint, Sequence
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .base import Base


class Security(Base):
    __tablename__ = "securities"

    id = Column(Integer, Sequence("securities_id_seq"), primary_key=True)
    symbol = Column(String(30), nullable=False, unique=True, index=True)
    company_name = Column(String(200), nullable=True)
    security_type = Column(String(10), nullable=False, index=True)  # STOCK, ETF, INDEX
    isin = Column(String(12), nullable=True, unique=True, index=True)
    face_value = Column(Numeric(10, 2), nullable=True)
    listing_date = Column(Date, nullable=True)
    issued_shares = Column(BigInteger, nullable=True)  # Nullable for ETF/INDEX
    industry = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    is_delisted = Column(Boolean, default=False)
    data_source = Column(String(30), default="BHAVCOPY_DISCOVERED")
    first_seen_date = Column(Date, nullable=True)
    last_seen_date = Column(Date, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    raw_prices = relationship("RawPrice", back_populates="security", primaryjoin="Security.id == RawPrice.security_id", foreign_keys="RawPrice.security_id", cascade="all, delete-orphan")
    adjusted_prices = relationship("AdjustedPrice", back_populates="security", primaryjoin="Security.id == AdjustedPrice.security_id", foreign_keys="AdjustedPrice.security_id", cascade="all, delete-orphan")
    indicators = relationship("Indicator", back_populates="security", primaryjoin="Security.id == Indicator.security_id", foreign_keys="Indicator.security_id", cascade="all, delete-orphan")
    corporate_actions = relationship("CorporateAction", back_populates="security", primaryjoin="Security.id == CorporateAction.security_id", foreign_keys="CorporateAction.security_id", cascade="all, delete-orphan")
    market_caps = relationship("MarketCap", back_populates="security", primaryjoin="Security.id == MarketCap.security_id", foreign_keys="MarketCap.security_id", cascade="all, delete-orphan")
    symbol_changes = relationship("SymbolChange", back_populates="security", primaryjoin="Security.id == SymbolChange.security_id", foreign_keys="SymbolChange.security_id", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Security(symbol={self.symbol}, type={self.security_type}, active={self.is_active})>"


class RawPrice(Base):
    __tablename__ = "raw_prices"
    __table_args__ = (
        UniqueConstraint("security_id", "trade_date", name="uq_raw_price_date"),
    )

    id = Column(Integer, Sequence("raw_prices_id_seq"), primary_key=True)
    security_id = Column(Integer, nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    open = Column(Numeric(12, 2), nullable=False)
    high = Column(Numeric(12, 2), nullable=False)
    low = Column(Numeric(12, 2), nullable=False)
    close = Column(Numeric(12, 2), nullable=False)
    last_price = Column(Numeric(12, 2), nullable=True)
    prev_close = Column(Numeric(12, 2), nullable=True)
    volume = Column(BigInteger, nullable=False)
    turnover = Column(Numeric(18, 2), nullable=True)
    total_trades = Column(BigInteger, nullable=True)

    # Relationships
    security = relationship("Security", back_populates="raw_prices", primaryjoin="Security.id == RawPrice.security_id", foreign_keys=[security_id])

    def __repr__(self):
        return f"<RawPrice(security_id={self.security_id}, date={self.trade_date}, close={self.close})>"


class AdjustedPrice(Base):
    __tablename__ = "adjusted_prices"
    __table_args__ = (
        UniqueConstraint("security_id", "trade_date", name="uq_adjusted_price_date"),
    )

    id = Column(Integer, Sequence("adjusted_prices_id_seq"), primary_key=True)
    security_id = Column(Integer, nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    adj_open = Column(Numeric(12, 4), nullable=False)
    adj_high = Column(Numeric(12, 4), nullable=False)
    adj_low = Column(Numeric(12, 4), nullable=False)
    adj_close = Column(Numeric(12, 4), nullable=False)
    adj_volume = Column(BigInteger, nullable=False)
    adjustment_factor = Column(Numeric(12, 6), nullable=False, default=1.0)

    # Relationships
    security = relationship("Security", back_populates="adjusted_prices", primaryjoin="Security.id == AdjustedPrice.security_id", foreign_keys=[security_id])

    def __repr__(self):
        return f"<AdjustedPrice(security_id={self.security_id}, date={self.trade_date}, adj_close={self.adj_close})>"

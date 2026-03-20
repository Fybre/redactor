from sqlalchemy import String, Integer, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class RedactionRegion(Base):
    __tablename__ = "redaction_regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    x0: Mapped[float] = mapped_column(Float, nullable=False)
    y0: Mapped[float] = mapped_column(Float, nullable=False)
    x1: Mapped[float] = mapped_column(Float, nullable=False)
    y1: Mapped[float] = mapped_column(Float, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    original_text: Mapped[str] = mapped_column(String, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[str] = mapped_column(String, nullable=False, default="auto")   # "auto" or "user"
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending") # "pending", "approved", "rejected"

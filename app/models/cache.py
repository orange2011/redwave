from datetime import datetime
from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class AlbumCache(Base):
    __tablename__ = "album_cache"

    cache_key: Mapped[str] = mapped_column(String, primary_key=True)  # "artist|album" lowercased
    artist: Mapped[str] = mapped_column(String, default="")
    album: Mapped[str] = mapped_column(String, default="")
    data_json: Mapped[str] = mapped_column(Text, default="{}")  # full album page data
    cached_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

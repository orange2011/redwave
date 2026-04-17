from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class AlbumRequest(Base):
    __tablename__ = "album_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    musicbrainz_id: Mapped[str] = mapped_column(String, nullable=False)
    artist: Mapped[str] = mapped_column(String, nullable=False)
    album: Mapped[str] = mapped_column(String, nullable=False)
    year: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    # pending | searching | found | downloading | completed | failed
    selected_torrent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qbt_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    torrent_options: Mapped[list["TorrentOption"]] = relationship("TorrentOption", back_populates="request", cascade="all, delete-orphan")


class TorrentOption(Base):
    __tablename__ = "torrent_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(Integer, ForeignKey("album_requests.id"), nullable=False)
    red_torrent_id: Mapped[int] = mapped_column(Integer, nullable=False)
    red_group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str | None] = mapped_column(String, nullable=True)
    encoding: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seeders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leechers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploader: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    request: Mapped["AlbumRequest"] = relationship("AlbumRequest", back_populates="torrent_options")

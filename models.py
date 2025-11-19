from sqlalchemy import Column, String, DateTime, Integer, Text, ForeignKey, Table, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base

def now_utc():
    return datetime.now(timezone.utc)

# 사용자 테이블
class User(Base):
    __tablename__ = "users"
    
    username = Column(String(100), primary_key=True)
    password = Column(String(255), nullable=False)
    nickname = Column(String(100), default="")
    extra = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=now_utc)
    
    # 관계
    following = relationship(
        "Follow",
        foreign_keys="Follow.follower_username",
        back_populates="follower",
        cascade="all, delete-orphan"
    )
    followers = relationship(
        "Follow",
        foreign_keys="Follow.followee_username",
        back_populates="followee",
        cascade="all, delete-orphan"
    )

# 채팅방 테이블
class Room(Base):
    __tablename__ = "rooms"
    
    id = Column(String(20), primary_key=True)  # r_xxxxxxxx 형식
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    last_message_text = Column(Text, nullable=True)
    last_message_from = Column(String(100), nullable=True)
    last_message_kind = Column(String(20), nullable=True)
    last_message_ts = Column(DateTime(timezone=True), nullable=True)
    
    # 관계
    members = relationship("RoomMember", back_populates="room", cascade="all, delete-orphan")
    messages = relationship("ChatLog", back_populates="room", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_room_name', 'name'),
    )

# 채팅방 멤버십 테이블
class RoomMember(Base):
    __tablename__ = "room_members"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(20), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    username = Column(String(100), ForeignKey("users.username", ondelete="CASCADE"), nullable=False)
    joined_at = Column(DateTime(timezone=True), default=now_utc)
    
    # 관계
    room = relationship("Room", back_populates="members")
    
    __table_args__ = (
        Index('idx_room_member', 'room_id', 'username', unique=True),
        Index('idx_username_rooms', 'username'),
    )

# 채팅 로그 테이블
class ChatLog(Base):
    __tablename__ = "chat_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(String(20), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    ts = Column(DateTime(timezone=True), default=now_utc, index=True)
    kind = Column(String(20), nullable=False)  # msg, dm, system
    from_user = Column(String(100), nullable=False)
    from_nickname = Column(String(100), default="")
    to_user = Column(String(100), nullable=True)  # DM인 경우에만
    text = Column(Text, default="")
    
    # 관계
    room = relationship("Room", back_populates="messages")
    
    __table_args__ = (
        Index('idx_room_ts', 'room_id', 'ts'),
        Index('idx_kind', 'kind'),
    )

# 팔로우 관계 테이블
class Follow(Base):
    __tablename__ = "follows"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_username = Column(String(100), ForeignKey("users.username", ondelete="CASCADE"), nullable=False)
    followee_username = Column(String(100), ForeignKey("users.username", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc)
    
    # 관계
    follower = relationship("User", foreign_keys=[follower_username], back_populates="following")
    followee = relationship("User", foreign_keys=[followee_username], back_populates="followers")
    
    __table_args__ = (
        Index('idx_follow_unique', 'follower_username', 'followee_username', unique=True),
        Index('idx_followee', 'followee_username'),
    )

from pydantic import BaseModel, Field
from dataclasses import dataclass, asdict


class LoginReq(BaseModel):
    username: str
    password: str
    nickname: str = ""

@dataclass(frozen=True)
class UserInfo:
    username: str
    password: str
    extra: str = ""
    nickname: str = ""


@dataclass(frozen=True)
class RoomInfo:
    name: str
    id: str
    user: set
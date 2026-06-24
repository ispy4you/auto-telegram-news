from pydantic import BaseModel


class SourceCreate(BaseModel):
    title: str
    username_or_url: str


class TargetCreate(BaseModel):
    title: str
    chat_id: str
    username: str | None = None
    enabled: bool = True
    auto_publish_enabled: bool = False
    default_mode: str = "manual"

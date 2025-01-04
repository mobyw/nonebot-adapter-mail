from pydantic import BaseModel, EmailStr, Field, SecretStr


class HostInfo(BaseModel):
    host: str = Field(alias="host")
    port: int = Field(alias="port")
    tls: bool = Field(alias="tls")


class BotInfo(BaseModel):
    id: EmailStr = Field(alias="id")
    name: str = Field(alias="name")
    password: SecretStr = Field(alias="password")
    subject: str = Field(alias="subject")
    imap: HostInfo = Field(alias="imap")
    smtp: HostInfo = Field(alias="smtp")


class Config(BaseModel):
    mail_bots: list[BotInfo] = Field(default_factory=list)

    class Config:
        extra = "ignore"

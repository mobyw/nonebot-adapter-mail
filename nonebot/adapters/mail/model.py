from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from .message import Message


class User(BaseModel):
    id: str
    name: Optional[str] = None


class Mail(BaseModel):
    id: str
    """
    Mail Message ID
    """
    sender: User
    """
    Mail sender
    """
    subject: str
    """
    Mail subject
    """
    recipients_to: list[User]
    """
    Mail recipients (To)
    """
    recipients_cc: list[User]
    """
    Mail recipients (Cc)
    """
    recipients_bcc: list[User]
    """
    Mail recipients (Bcc)
    """
    date: Optional[datetime]
    """
    Mail date
    """
    timezone: Optional[float]
    """
    Mail timezone offset
    """
    message: "Message"
    """
    Plain text mail message with attachments
    """
    original_message: "Message"
    """
    HTML mail message with attachments
    """
    in_reply_to: Optional[str] = None
    """
    Message ID which this mail is replying to
    """
    references: Optional[list[str]] = None
    """
    Message IDs which this mail references
    """
    reply_to: Optional[list[User]] = None
    """
    Mail addresses to reply to
    """

from typing import Optional
from typing_extensions import override

from nonebot.adapters import Event as BaseEvent
from nonebot.compat import model_dump
from nonebot.utils import escape_tag

from .message import Message
from .model import Mail


class Event(BaseEvent):

    @override
    def get_event_name(self) -> str:
        return self.get_type()

    @override
    def get_event_description(self) -> str:
        return escape_tag(str(model_dump(self)))

    @override
    def get_message(self) -> Message:
        raise ValueError("Event has no message!")

    @override
    def get_user_id(self) -> str:
        raise ValueError("Event has no context!")

    @override
    def get_session_id(self) -> str:
        raise ValueError("Event has no context!")

    @override
    def is_tome(self) -> bool:
        return False


# Message Event
class MessageEvent(Event, Mail):
    to_me: bool = False

    @override
    def get_type(self) -> str:
        return "message"

    @override
    def is_tome(self) -> bool:
        return self.to_me


class NewMailMessageEvent(MessageEvent):
    reply: Optional[Mail] = None

    @override
    def get_user_id(self) -> str:
        return str(self.sender.id)

    @override
    def get_session_id(self) -> str:
        return str(self.sender.id)

    @override
    def get_event_description(self) -> str:
        return escape_tag(
            f"Message {self.id} from {self.sender.name}<{self.sender.id}>: "
            f"{self.get_message()}"
        )

    @override
    def get_message(self) -> Message:
        return self.message


__all__ = [
    "Event",
    "MessageEvent",
    "NewMailMessageEvent",
]

from io import BytesIO
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Iterable
from typing_extensions import override
from typing import TYPE_CHECKING, Union, Optional, TypedDict

from nonebot.adapters import Message as BaseMessage
from nonebot.adapters import MessageSegment as BaseMessageSegment


class MessageSegment(BaseMessageSegment["Message"]):
    @classmethod
    @override
    def get_message_class(cls) -> type["Message"]:
        return Message

    @override
    def __add__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, other: Union[str, "MessageSegment", Iterable["MessageSegment"]]
    ) -> "Message":
        return Message(self) + (
            MessageSegment.text(other) if isinstance(other, str) else other
        )

    @override
    def __radd__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, other: Union[str, "MessageSegment", Iterable["MessageSegment"]]
    ) -> "Message":
        return (
            MessageSegment.text(other) if isinstance(other, str) else Message(other)
        ) + self

    @override
    def __str__(self) -> str:
        if self.is_text():
            return self.data.get("text", "")
        params = ", ".join(
            [f"{k}={v!s}" for k, v in self.data.items() if v is not None]
        )
        return f"[{self.type}{':' if params else ''}{params}]"

    @override
    def is_text(self) -> bool:
        return self.type == "text"

    @staticmethod
    def text(text: str) -> "Text":
        return Text("text", {"text": text})

    @staticmethod
    def html(html: str) -> "Html":
        return Html("html", {"html": html})

    @staticmethod
    def attachment(
        data: Union[bytes, BytesIO, Path],
        name: str,
        content_type: Optional[str] = None,
    ) -> "Attachment":
        if isinstance(data, Path):
            data_bytes = data.read_bytes()
        elif isinstance(data, BytesIO):
            data_bytes = data.getvalue()
        else:
            data_bytes = data
        return Attachment(
            "attachment",
            {
                "data": data_bytes,
                "name": name,
                "content_type": content_type,
            },
        )


class _TextData(TypedDict):
    text: str


@dataclass
class Text(MessageSegment):
    if TYPE_CHECKING:
        data: _TextData  # pyright: ignore[reportGeneralTypeIssues]

    @override
    def __str__(self) -> str:
        return self.data["text"]

    @override
    def is_text(self) -> bool:
        return True


class _HtmlData(TypedDict):
    html: str


@dataclass
class Html(MessageSegment):
    if TYPE_CHECKING:
        data: _HtmlData  # pyright: ignore[reportGeneralTypeIssues]

    @override
    def __str__(self) -> str:
        return f"[html:{self.data['html']}]"


class _AttachmentData(TypedDict):
    data: bytes
    name: str
    content_type: Optional[str]


@dataclass
class Attachment(MessageSegment):
    if TYPE_CHECKING:
        data: _AttachmentData  # pyright: ignore[reportGeneralTypeIssues]

    @override
    def __str__(self) -> str:
        return f"[attachment:{self.data['name']}, ...]"


class Message(BaseMessage[MessageSegment]):
    @classmethod
    @override
    def get_segment_class(cls) -> type[MessageSegment]:
        return MessageSegment

    @override
    def __add__(
        self, other: Union[str, MessageSegment, Iterable[MessageSegment]]
    ) -> "Message":
        return super().__add__(
            MessageSegment.text(other) if isinstance(other, str) else other
        )

    @override
    def __radd__(
        self, other: Union[str, MessageSegment, Iterable[MessageSegment]]
    ) -> "Message":
        return super().__radd__(
            MessageSegment.text(other) if isinstance(other, str) else other
        )

    @staticmethod
    @override
    def _construct(msg: str) -> Iterable[MessageSegment]:
        if msg:
            yield Text("text", {"text": msg})

    @override
    def __str__(self) -> str:
        result = ""
        for seg in self:
            result += (
                str(seg)
                if seg.type != "attachment"
                else f"[attachment:{seg.data['name']}]"
            )
        return result

    @override
    def __repr__(self) -> str:
        return f"{self.__str__()!r}"

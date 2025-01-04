from typing import Literal, Optional, TypedDict, Union

from aioimaplib import Response as ImapResponse
from aiosmtplib.response import SMTPResponse

from nonebot.exception import ActionFailed as BaseActionFailed
from nonebot.exception import AdapterException
from nonebot.exception import ApiNotAvailable as BaseApiNotAvailable
from nonebot.exception import NetworkError as BaseNetworkError
from nonebot.exception import NoLogException as BaseNoLogException


class MailAdapterException(AdapterException):
    def __init__(self):
        super().__init__("mail")


class Response(TypedDict):
    code: str
    message: str


class ActionFailed(BaseActionFailed, MailAdapterException):
    def __init__(
        self,
        response: Union[tuple[str, ImapResponse], dict[str, SMTPResponse]],
    ):
        self.type: Literal["IMAP", "SMTP"] = "IMAP"
        self.response: dict[str, Response] = {}
        if (
            isinstance(response, tuple)
            and isinstance(response[0], str)
            and isinstance(response[1], ImapResponse)
        ):
            self.type = "IMAP"
            self.response[response[0]] = {
                "code": response[1].result,
                "message": ", ".join(
                    [str(line.decode("utf-8")) for line in response[1].lines]
                ),
            }
        elif isinstance(response, dict) and all(
            isinstance(k, str) and isinstance(v, SMTPResponse)
            for k, v in response.items()
        ):
            self.type = "SMTP"
            for k, v in response.items():
                self.response[k] = {"code": str(v.code), "message": str(v.message)}
        else:
            raise ValueError("Invalid response type")

    @property
    def message(self) -> Optional[str]:
        return None if self.response == {} else str(self.response)

    def __repr__(self) -> str:
        return f"<ActionFailed: {self.type} {self.message}>"

    def __str__(self):
        return self.__repr__()


class NoLogException(BaseNoLogException, MailAdapterException):
    pass


class NetworkError(BaseNetworkError, MailAdapterException):
    pass


class ApiNotAvailable(BaseApiNotAvailable, MailAdapterException):
    pass


class UninitializedException(MailAdapterException):
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"<UninitializedException: {self.name} is not initialized>"

    def __str__(self):
        return self.__repr__()

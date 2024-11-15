import email.message
import email.mime.multipart
from collections.abc import Sequence
from typing_extensions import override
from typing import TYPE_CHECKING, Any, Union, NoReturn, Optional

import aioimaplib
import aiosmtplib
import mailparser

from nonebot.utils import escape_tag
from nonebot.message import handle_event
from nonebot.adapters import Bot as BaseBot

from .log import log
from .config import BotInfo
from .model import Mail, User
from .message import Message, MessageSegment
from .utils import escape_bytelines, extract_mail_parts
from .event import Event, MessageEvent, NewMailMessageEvent
from .exception import ActionFailed, NetworkError, UninitializedException

if TYPE_CHECKING:
    from .adapter import Adapter


def _check_to_me(
    bot: "Bot",
    event: MessageEvent,
):
    if isinstance(event, NewMailMessageEvent) and bot.bot_info.id in {
        i.id for i in event.recipients_to
    }:
        event.to_me = True


async def _check_reply(
    bot: "Bot",
    event: MessageEvent,
):
    if not isinstance(event, NewMailMessageEvent) or event.in_reply_to is None:
        return
    try:
        event.reply = await bot.get_mail_of_id(
            mail_id=event.in_reply_to,
        )
        if event.reply and event.reply.sender.id == bot.bot_info.id:
            event.to_me = True
    except Exception as e:
        log(
            "WARNING",
            (
                f"<y>Bot {escape_tag(bot.self_id)}</y> "
                "failed to fetch the reply mail."
            ),
            e,
        )


def parse_byte_mail(byte_mail: bytes) -> Mail:
    """
    Parse the mail and return the Mail object.

    :param mail: The mail to parse.
    :return: The Mail object.
    """
    mail = mailparser.parse_from_bytes(byte_mail)

    return Mail(
        id=str(mail.message_id),
        sender=User(
            id=mail.from_[0][1],
            name=mail.from_[0][0],
        ),
        recipients_to=[
            User(
                id=recipient[1],
                name=recipient[0],
            )
            for recipient in mail.to
        ],
        recipients_cc=[
            User(
                id=recipient[1],
                name=recipient[0],
            )
            for recipient in mail.headers.get("Cc", [])
        ],
        recipients_bcc=[
            User(
                id=recipient[1],
                name=recipient[0],
            )
            for recipient in mail.headers.get("Bcc", [])
        ],
        date=mail.date,
        timezone=float(mail.timezone) if mail.timezone else None,
        message=(
            Message([MessageSegment.text(text) for text in mail.text_plain])
            + Message(
                [
                    MessageSegment.attachment(
                        attachment["filename"],
                        attachment["binary"],
                        attachment["payload"],
                        attachment["mail_content_type"],
                    )
                    for attachment in mail.attachments
                ]
            )
        ),
        original_message=(
            Message([MessageSegment.html(html) for html in mail.text_html])
            + Message(
                [
                    MessageSegment.attachment(
                        attachment["filename"],
                        attachment["binary"],
                        attachment["payload"],
                        attachment["mail_content_type"],
                    )
                    for attachment in mail.attachments
                ],
            )
        ),
        in_reply_to=str(mail.in_reply_to) if mail.in_reply_to else None,
    )


class Bot(BaseBot):
    @override
    def __init__(self, adapter: "Adapter", self_id: str, bot_info: BotInfo):
        super().__init__(adapter, self_id)
        self.bot_info: BotInfo = bot_info
        self.imap_client: Optional[aioimaplib.IMAP4] = None

    @override
    def __getattr__(self, name: str) -> NoReturn:
        raise AttributeError(
            f'"{self.__class__.__name__}" object has no attribute "{name}"'
        )

    @property
    def type(self) -> str:
        return "Mail"

    @override
    async def send(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        event: Event,
        message: Union[str, Message, MessageSegment],
        **kwargs,
    ) -> Any:
        """
        Send a message to the event.

        - event: The event to reply.
        - message: The message to send.
        """
        if not isinstance(event, MessageEvent):
            raise ValueError("event is not a MessageEvent")
        if isinstance(message, str):
            message = Message([MessageSegment.text(message)])
        elif isinstance(message, MessageSegment):
            message = Message([message])
        await self.send_mail(
            message=message,
            recipients=[event.sender.id],
        )

    async def send_mail(
        self,
        message: Union[Message, email.message.EmailMessage],
        subject: Optional[str] = None,
        recipients: Optional[Sequence[str]] = None,
    ) -> None:
        """
        Send a mail to the given recipients.

        - message: The message to send.
        - subject: The subject of the mail.
        - recipients: The list of recipients.
        """
        if not subject:
            subject = self.bot_info.subject
        if isinstance(message, Message):
            if not recipients:
                raise ValueError("recipients_to is required when sending a Message")
            _message = email.mime.multipart.MIMEMultipart()
            _message["From"] = self.bot_info.id
            _message["To"] = ", ".join(recipients)
            _message["Subject"] = subject
            parts = extract_mail_parts(message)
            for part in parts:
                _message.attach(part)
        else:
            _message = message
        try:
            response = await aiosmtplib.send(
                _message,
                hostname=self.bot_info.smtp.host,
                port=self.bot_info.smtp.port,
                use_tls=self.bot_info.smtp.tls,
                username=self.bot_info.id,
                password=self.bot_info.password.get_secret_value(),
            )
        except Exception as e:
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "failed to connect to SMTP server: "
                    f"{escape_tag(str(e))}"
                ),
            )
            raise NetworkError()
        log(
            "TRACE",
            (
                f"<y>Bot {escape_tag(self.self_id)}</y> "
                f"mail sent to {escape_tag(str(recipients))}: "
                + escape_tag(str(response))
            ),
        )
        if response[0] != {}:
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "failed to send mail: "
                    f"{escape_tag(str(response[0]))}"
                ),
            )
            raise ActionFailed(response[0])

    async def login(self) -> bool:
        """
        Login to the IMAP server.
        """
        if not self.imap_client:
            raise UninitializedException("IMAP client")
        await self.imap_client.wait_hello_from_server()
        # login to the server
        response = await self.imap_client.login(
            self.bot_info.id, self.bot_info.password.get_secret_value()
        )
        # check if login was successful
        if not response.result == "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    f"error in logging in: "
                    f"{escape_bytelines(response.lines)}"
                    "</bg #f8bbd0></r>"
                ),
            )
            return False
        return True

    async def logout(self) -> bool:
        """
        Logout from the IMAP server.
        """
        if not self.imap_client:
            raise UninitializedException("IMAP client")
        response = await self.imap_client.logout()
        if not response.result == "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    f"error in logging out: "
                    f"{escape_bytelines(response.lines)}"
                    "</bg #f8bbd0></r>"
                ),
            )
            return False
        return True

    async def select_mailbox(self, mailbox: str = "INBOX") -> bool:
        """
        Select the mailbox on the IMAP server.

        - mailbox: The mailbox to select. Default is "INBOX".
        """
        if not self.imap_client:
            raise UninitializedException("IMAP client")
        if mailbox.startswith('"') and mailbox.endswith('"'):
            mailbox = mailbox[1:-1]
        response = await self.imap_client.select(
            mailbox if " " not in mailbox else f'"{mailbox}"'
        )
        if not response.result == "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    f"error in selecting mailbox: "
                    f"{escape_bytelines(response.lines)}"
                    "</bg #f8bbd0></r>"
                ),
            )
            return False
        log(
            "TRACE",
            (
                f"<y>Bot {escape_tag(self.self_id)}</y> "
                f"mailbox {escape_tag(mailbox)} selected: "
                + escape_bytelines(response.lines)
            ),
        )
        return True

    async def get_unseen_uids(self) -> list[str]:
        """
        Get the UIDs of unseen mails in current mailbox.
        """
        if not self.imap_client:
            raise UninitializedException("IMAP client")
        response = await self.imap_client.search("UNSEEN")
        if response.result != "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    "error in fetching unseen mails: "
                    f"{escape_bytelines(response.lines)}"
                    "</bg #f8bbd0></r>"
                ),
            )
            raise ActionFailed((self.self_id, response))
        if len(response.lines) > 0 and response.lines[0]:
            log(
                "TRACE",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    f"unseen mail UIDs: {escape_bytelines(response.lines)}"
                ),
            )
        else:
            log(
                "TRACE",
                f"<y>Bot {escape_tag(self.self_id)}</y> no unseen mails",
            )
        return response.lines[0].decode().split()

    async def get_mail_of_uid(self, mail_uid: str) -> Optional[Mail]:
        """
        Get the mail of the given UID from the current mailbox.

        - mail_uid: The UID of the mail to fetch.
        """
        if not self.imap_client:
            raise UninitializedException("IMAP client")
        log(
            "TRACE",
            (
                f"<y>Bot {escape_tag(self.self_id)}</y> "
                f"fetching mail UID: {escape_tag(mail_uid)}"
            ),
        )
        response = await self.imap_client.fetch(mail_uid, "(RFC822)")
        if response.result != "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    f"error in fetching mail UID {mail_uid}: "
                    f"{escape_bytelines(response.lines)}"
                    "</bg #f8bbd0></r>"
                ),
            )
            raise ActionFailed((self.self_id, response))
        log(
            "TRACE",
            (
                "<y>Bot {escape_tag(self.self_id)}</y> "
                f"mail UID {escape_tag(mail_uid)} fetched"
            ),
        )
        # Parse the mail
        if len(response.lines) < 2:
            log(
                "WARNING",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    f"mail UID {escape_tag(mail_uid)} not found"
                ),
            )
            return None
        mail = parse_byte_mail(response.lines[1])
        return mail

    async def get_mail_of_id_in_mailbox(
        self, mail_id: str, mailbox: str = "INBOX"
    ) -> Optional[Mail]:
        """
        Get the mail of the given Message-ID from the given mailbox.

        - mail_id: The Message-ID of the mail to search for.
        - mailbox: The mailbox to search in. Default is "INBOX".
        """
        if not self.imap_client:
            raise UninitializedException("IMAP client")
        log(
            "TRACE",
            (
                f"<y>Bot {escape_tag(self.self_id)}</y> "
                f"searching mail ID: {escape_tag(mail_id)} "
                f"in mailbox: {escape_tag(mailbox)}"
            ),
        )
        if not await self.select_mailbox(mailbox):
            return None
        response = await self.imap_client.search(f"HEADER Message-ID {mail_id}")
        log(
            "TRACE",
            (
                f"<y>Bot {escape_tag(self.self_id)}</y> "
                f"mail ID {escape_tag(mail_id)} search result: "
                + escape_bytelines(response.lines)
            ),
        )
        if response.result != "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    f"error in searching mail ID {escape_tag(mail_id)}: "
                    f"{escape_bytelines(response.lines)}"
                    "</bg #f8bbd0></r>"
                ),
            )
            raise ActionFailed((self.self_id, response))
        if not response.lines or not response.lines[0].decode():
            log(
                "TRACE",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    f"mail ID {escape_tag(mail_id)} not found "
                    f"in mailbox {escape_tag(mailbox)}"
                ),
            )
            return None
        mail_uid = response.lines[0].decode()
        return await self.get_mail_of_uid(mail_uid)

    async def get_mail_of_id(self, mail_id: str) -> Optional[Mail]:
        """
        Get the mail of the given Message-ID from the INBOX or Sent mailboxes.

        - mail_id: The Message-ID of the mail to search for.
        """
        if not self.imap_client or not self.imap_client.protocol:
            raise UninitializedException("IMAP client")
        log(
            "TRACE",
            (
                f"<y>Bot {escape_tag(self.self_id)}</y> "
                f"searching mail ID: {escape_tag(mail_id)}"
            ),
        )
        # try to get mail from INBOX
        mail = await self.get_mail_of_id_in_mailbox(mail_id)
        if mail:
            return mail
        # try to get mail from Sent
        response = await self.imap_client.list(
            '""', "*"  # pyright: ignore[reportArgumentType]
        )
        if response.result != "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    "error in listing mailboxes"
                    "</bg #f8bbd0></r>"
                ),
            )
            raise ActionFailed((self.self_id, response))
        sent_mailbox_list = [
            str(i.decode().split(' "/" ')[-1])
            for i in response.lines
            if i.startswith(b"(\\Sent)")
        ]
        for mailbox in sent_mailbox_list:
            mail = await self.get_mail_of_id_in_mailbox(mail_id, mailbox)
            if mail:
                break
        # switch back to INBOX
        await self.imap_client.select("INBOX")
        return mail

    async def handle_event(self, event: Event) -> None:
        if isinstance(event, MessageEvent):
            _check_to_me(self, event)
            await _check_reply(self, event)
        await handle_event(self, event)

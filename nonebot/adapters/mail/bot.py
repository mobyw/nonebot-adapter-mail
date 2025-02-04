import asyncio
import email.message
import email.mime.multipart
from typing import TYPE_CHECKING, Any, NoReturn, Optional, Union
from typing_extensions import override

import aioimaplib
import aiosmtplib

from nonebot.adapters import Bot as BaseBot
from nonebot.message import handle_event
from nonebot.utils import escape_tag

from .config import BotInfo
from .event import Event, MessageEvent, NewMailMessageEvent
from .exception import ActionFailed, NetworkError, UninitializedException
from .log import log
from .message import Message, MessageSegment
from .model import Mail
from .utils import escape_bytelines, extract_mail_parts, parse_byte_mail

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
        event.reply = await bot.fetch_mail_of_id(
            mail_id=event.in_reply_to,
        )
        if event.reply and event.reply.sender.id == bot.bot_info.id:
            event.to_me = True
    except Exception as e:
        log(
            "WARNING",
            (f"<y>Bot {escape_tag(bot.self_id)}</y> failed to fetch the reply mail."),
            e,
        )


class Bot(BaseBot):
    @override
    def __init__(self, adapter: "Adapter", self_id: str, bot_info: BotInfo):
        super().__init__(adapter, self_id)
        self.bot_info: BotInfo = bot_info
        self.imap_client: Optional[aioimaplib.IMAP4] = None
        self.mailbox: Optional[str] = None
        self.readonly: bool = False

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
        reply: Optional[bool] = None,
        **kwargs,
    ) -> Any:
        """
        Send a message to the event.

        - `event`: The event to reply.
        - `message`: The message to send.
        - `reply`: Whether using default reply settings.
        - `kwargs`: Other arguments.
            - `cc`: The list of CC recipients.
            - `bcc`: The list of BCC recipients.
            - `subject`: The subject of the mail.
            - `in_reply_to`: The Message ID of the mail to reply to.
            - `references`: The list of Message IDs of the mails in the thread.
            - `reply_to`: The list of addresses to recommend recipients to reply to.
        """
        if not isinstance(event, MessageEvent):
            raise RuntimeError("Event cannot be replied to!")
        if isinstance(message, str):
            message = Message([MessageSegment.text(message)])
        elif isinstance(message, MessageSegment):
            message = Message([message])
        if "reply" in message:
            reply_id = message["reply", 0].data.get("id")
            if reply_id == event.id:
                reply = True if reply is None else reply
                message = message.exclude("reply")
        if reply:
            if kwargs.get("subject") is None:
                kwargs["subject"] = f"Re: {event.subject}"
            if kwargs.get("in_reply_to") is None:
                kwargs["in_reply_to"] = event.id
            if kwargs.get("references") is None:
                kwargs["references"] = [event.id]
        await self.send_mail(
            message=message,
            recipient=event.sender.id,
            **kwargs,
        )

    async def send_to(
        self,
        recipient: Union[str, list[str]],
        message: Union[str, Message, MessageSegment],
        **kwargs,
    ) -> None:
        """
        Send a mail to the given recipient(s).

        - `recipient`: The recipient(s) to send the mail to.
        - `message`: The message to send.
        - `kwargs`: Other arguments.
            - `cc`: The list of CC recipients.
            - `bcc`: The list of BCC recipients.
            - `subject`: The subject of the mail.
            - `in_reply_to`: The Message ID of the mail to reply to.
            - `references`: The list of Message IDs of the mails in the thread.
            - `reply_to`: The list of addresses to recommend recipients to reply to.
        """
        recipient = [recipient] if isinstance(recipient, str) else recipient
        if isinstance(message, str):
            message = Message([MessageSegment.text(message)])
        elif isinstance(message, MessageSegment):
            message = Message([message])
        await self.send_mail(
            message=message,
            recipient=recipient,
            **kwargs,
        )

    async def send_mail(
        self,
        message: Union[Message, email.message.EmailMessage],
        recipient: Union[str, list[str], None] = None,
        cc: Union[str, list[str], None] = None,
        bcc: Union[str, list[str], None] = None,
        subject: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        references: Union[str, list[str], None] = None,
        reply_to: Union[str, list[str], None] = None,
    ) -> None:
        """
        Send a mail to the given recipients.

        - `message`: The message to send.
        - `subject`: The subject of the mail.
        - `recipients`: The list of recipients to send the mail to.
        - `cc`: The list of CC recipients.
        - `bcc`: The list of BCC recipients.
        - `in_reply_to`: The Message ID of the mail to reply to.
        - `references`: The list of Message IDs of the mails in the thread.
        - `reply_to`: The list of addresses to recommend recipients to reply to.
        """
        if isinstance(message, Message):
            if not recipient:
                raise ValueError("recipient is required when sending a Message")
            if "subject" in message and subject is None:
                subject = message["subject", 0].data["subject"]
            if "reply" in message:
                reply_id = message["reply", 0].data["id"]
                if subject is None:
                    reply_mail = await self.fetch_mail_of_id(str(reply_id))
                    reply_subject = reply_mail.subject if reply_mail else None
                    subject = f"Re: {reply_subject}" if reply_subject else None
                in_reply_to = str(reply_id) if in_reply_to is None else in_reply_to
                references = [str(reply_id)] if references is None else references
            cc = [cc] if isinstance(cc, str) else cc if cc else []
            bcc = [bcc] if isinstance(bcc, str) else bcc if bcc else []
            for segment in message["cc"]:
                cc.append(segment.data["id"])
            for segment in message["bcc"]:
                bcc.append(segment.data["id"])
            _message = email.mime.multipart.MIMEMultipart()
            _message["From"] = f"{self.bot_info.name} <{self.bot_info.id}>"
            _message["To"] = (
                ", ".join(recipient) if isinstance(recipient, list) else recipient
            )
            if cc:
                _message["Cc"] = ", ".join(cc)
            if bcc:
                _message["Bcc"] = ", ".join(bcc)
            _message["Subject"] = subject or self.bot_info.subject
            if in_reply_to:
                _message["In-Reply-To"] = in_reply_to
            if references:
                _message["References"] = (
                    ", ".join(references)
                    if isinstance(references, list)
                    else references
                )
            elif in_reply_to:
                _message["References"] = in_reply_to
            if reply_to:
                _message["Reply-To"] = (
                    ", ".join(reply_to) if isinstance(reply_to, list) else reply_to
                )
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
            if isinstance(e, aiosmtplib.SMTPRecipientsRefused):
                raise ActionFailed(
                    {
                        i.recipient: aiosmtplib.SMTPResponse(i.code, i.message)
                        for i in e.recipients
                    }
                )
            elif isinstance(e, aiosmtplib.SMTPResponseException):
                raise ActionFailed(
                    {self.bot_info.id: aiosmtplib.SMTPResponse(e.code, e.message)}
                )
            elif isinstance(e, aiosmtplib.SMTPException):
                raise NetworkError()
            else:
                raise e

        log(
            "TRACE",
            (
                f"<y>Bot {escape_tag(self.self_id)}</y> "
                f"mail sent to {escape_tag(str(recipient))}: "
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
        # report id to avoid unsafe in 163 mails
        assert self.imap_client.protocol is not None
        response = await asyncio.wait_for(
            self.imap_client.protocol.execute(
                aioimaplib.Command(
                    "ID",
                    self.imap_client.protocol.new_tag(),
                    '("name" "nonebot" "version" "2")',
                )
            ),
            self.imap_client.timeout,
        )
        if not response.result == "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    f"error in reporting client ID: "
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
        await self.imap_client.close()
        return True

    async def select_mailbox(
        self, mailbox: str = "INBOX", readonly: bool = False
    ) -> bool:
        """
        Select the mailbox on the IMAP server.

        - `mailbox`: The mailbox to select. Default is "INBOX".
        """
        if not self.imap_client:
            raise UninitializedException("IMAP client")
        if mailbox.startswith('"') and mailbox.endswith('"'):
            mailbox = mailbox[1:-1]
        mailbox = mailbox if " " not in mailbox else f'"{mailbox}"'
        if self.mailbox == mailbox and self.readonly == readonly:
            return True
        response = (
            await self.imap_client.select(mailbox)
            if not readonly
            else await self.imap_client.examine(mailbox)
        )
        if not response.result == "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    f"error in selecting mailbox: "
                    if not readonly
                    else "error in examining mailbox: "
                    f"{escape_bytelines(response.lines)}"
                    "</bg #f8bbd0></r>"
                ),
            )
            return False
        log(
            "TRACE",
            (
                f"<y>Bot {escape_tag(self.self_id)}</y> "
                f"mailbox {escape_tag(mailbox)} selected ({readonly=}): "
                + escape_bytelines(response.lines)
            ),
        )
        self.mailbox = mailbox
        self.readonly = readonly
        return True

    async def fetch_mail_of_uid(self, mail_uid: str) -> Optional[Mail]:
        """
        Get the mail of the given UID from the current mailbox.

        - `mail_uid`: The UID of the mail to fetch.
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

    async def fetch_mail_list(self, key: str = "ALL") -> list[Mail]:
        """
        Fetch mails in current mailbox with the given IMAP search key.

        Note:

        - This will mark the mails as seen.
        - The default key is "ALL".
        - Available keys: https://www.rfc-editor.org/rfc/rfc3501#section-6.4.4
        """
        if not self.imap_client:
            raise UninitializedException("IMAP client")
        response = await self.imap_client.search(key)
        if response.result != "OK":
            log(
                "ERROR",
                (
                    f"<y>Bot {escape_tag(self.self_id)}</y> "
                    "<r><bg #f8bbd0>"
                    "error in fetching seen mails: "
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
                    f"seen mail UIDs: {escape_bytelines(response.lines)}"
                ),
            )
        else:
            log(
                "TRACE",
                f"<y>Bot {escape_tag(self.self_id)}</y> no seen mails",
            )
        mail_uids = response.lines[0].decode().split()
        mails: list[Mail] = []
        for mail_uid in mail_uids:
            mail = await self.fetch_mail_of_uid(mail_uid)
            if mail:
                mails.append(mail)
        return mails

    async def fetch_seen_mail_list(self) -> list[Mail]:
        """
        Fetch seen mails in current mailbox.
        """
        return await self.fetch_mail_list("SEEN")

    async def fetch_unseen_mail_list(self) -> list[Mail]:
        """
        Fetch unseen mails in current mailbox and mark them as seen.
        """
        return await self.fetch_mail_list("UNSEEN")

    async def fetch_mail_of_id_in_mailbox(
        self, mail_id: str, mailbox: str = "INBOX"
    ) -> Optional[Mail]:
        """
        Get the mail of the given Message ID from the given mailbox.

        - `mail_id`: The Message ID of the mail to search for.
        - `mailbox`: The mailbox to search in. Default is "INBOX".
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
        return await self.fetch_mail_of_uid(mail_uid)

    async def fetch_mail_of_id(self, mail_id: str) -> Optional[Mail]:
        """
        Get the mail of the given Message ID from the INBOX or Sent mailboxes.

        - `mail_id`: The Message ID of the mail to search for.
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
        mail = await self.fetch_mail_of_id_in_mailbox(mail_id)
        if mail:
            return mail
        # try to get mail from Sent
        response = await self.imap_client.list(
            '""',
            "*",  # pyright: ignore[reportArgumentType]
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
            mail = await self.fetch_mail_of_id_in_mailbox(mail_id, mailbox)
            if mail:
                break
        # switch back to INBOX
        await self.select_mailbox()
        return mail

    async def handle_event(self, event: Event) -> None:
        if isinstance(event, MessageEvent):
            _check_to_me(self, event)
            await _check_reply(self, event)
        await handle_event(self, event)

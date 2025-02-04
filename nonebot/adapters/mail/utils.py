import base64
import email.encoders
import email.message
import email.mime.base
import email.mime.text
import html

import mailparser

from nonebot.utils import escape_tag

from .message import Attachment, Message, MessageSegment
from .model import Mail, User


def escape_bytelines(s: list[bytearray]) -> str:
    """
    Escape a list of bytearrays to a string.

    - `s`: The list of bytearrays to escape.
    """
    return f"[{escape_tag(', '.join([i.decode() for i in s]))}]"


def extract_mail_parts(message: Message) -> list[email.message.EmailMessage]:
    """
    Extract email parts from a Message object.

    - `message`: The Message object to extract.
    """
    text: str = ""
    attachments = []
    contains_html = any(segment.type == "html" for segment in message)
    for segment in message:
        if segment.type == "text":
            text += (
                segment.data["text"]
                if not contains_html
                else html.escape(segment.data["text"])
            )
        elif segment.type == "html":
            text += segment.data["html"]
        elif segment.type == "attachment":
            attachments.append(segment)
    parts = []
    if contains_html:
        parts.append(email.mime.text.MIMEText(text, "html"))
    else:
        parts.append(email.mime.text.MIMEText(text))
    for attachment in attachments:
        if attachment.data["content_type"] and "/" in attachment.data["content_type"]:
            main_type, sub_type = attachment.data["content_type"].split("/")
        else:
            main_type, sub_type = "application", "octet-stream"
        part = email.mime.base.MIMEBase(main_type, sub_type)
        part.set_payload(attachment.data["data"])
        email.encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{attachment.data["name"]}"',
        )
        parts.append(part)
    return parts


def parse_attachment(attachment) -> Attachment:
    """
    Parse the attachment and return the Attachment object.

    - `attachment`: The attachment to parse.
    """

    data = (
        base64.b64decode(attachment["payload"])
        if attachment["binary"]
        else attachment["payload"]
    )

    if isinstance(data, str):
        data = data.encode()

    return MessageSegment.attachment(
        data,
        attachment["filename"],
        attachment["mail_content_type"],
    )


def parse_byte_mail(byte_mail: bytes) -> Mail:
    """
    Parse the mail and return the Mail object.

    - `byte_mail`: The byte mail to parse.
    """

    def parse_user(user):
        return User(
            id=user[1],
            name=user[0],
        )

    mail = mailparser.parse_from_bytes(byte_mail)

    sender = User(
        id=mail.from_[0][1],
        name=mail.from_[0][0],
    )
    if isinstance(mail.subject, str):
        subject = mail.subject
    else:
        subject = str(mail.subject[0]) if mail.subject else ""
    recipients_to = [parse_user(u) for u in mail.to]
    recipients_cc = [parse_user(u) for u in mail.headers.get("Cc", [])]
    recipients_bcc = [parse_user(u) for u in mail.headers.get("Bcc", [])]
    reply_to = [parse_user(u) for u in mail.headers.get("Reply-To", [])]
    message = Message(
        [MessageSegment.text(text) for text in mail.text_plain]
    ) + Message([parse_attachment(attachment) for attachment in mail.attachments])
    original_message = Message(
        [MessageSegment.html(html) for html in mail.text_html]
    ) + Message(
        [parse_attachment(attachment) for attachment in mail.attachments],
    )

    if str(message) == "":
        # no plain text provided by the mail
        message = Message([MessageSegment.text("")])

    return Mail(
        id=str(mail.message_id),
        sender=sender,
        subject=subject,
        recipients_to=recipients_to,
        recipients_cc=recipients_cc,
        recipients_bcc=recipients_bcc,
        date=mail.date,
        timezone=float(mail.timezone) if mail.timezone else None,
        message=message,
        original_message=original_message,
        in_reply_to=str(mail.in_reply_to) if mail.in_reply_to else None,
        references=(
            [str(reference) for reference in mail.references]
            if mail.references
            else None
        ),
        reply_to=reply_to if reply_to else None,
    )

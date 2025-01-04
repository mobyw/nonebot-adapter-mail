import asyncio
from typing import Any
from typing_extensions import override

from aioimaplib import aioimaplib
from nonebot.drivers import Driver
from nonebot.utils import escape_tag
from nonebot.compat import model_dump

from nonebot.adapters import Adapter as BaseAdapter

from .bot import Bot
from .log import log
from .config import Config, BotInfo
from .event import NewMailMessageEvent
from .exception import ApiNotAvailable

CHECK_MAIL_INTERVAL = 3.0
RECONNECT_INTERVAL = 3.0


class Adapter(BaseAdapter):
    @override
    def __init__(self, driver: Driver, **kwargs: Any) -> None:
        super().__init__(driver, **kwargs)
        self.mail_config: Config = Config(**model_dump(self.config))
        self.tasks: set[asyncio.Task] = set()
        self.setup()

    @classmethod
    @override
    def get_name(cls) -> str:
        return "Mail"

    def setup(self) -> None:
        self.on_ready(self.startup)
        self.driver.on_shutdown(self.shutdown)

    async def startup(self) -> None:
        for bot in self.mail_config.mail_bots:
            task = asyncio.create_task(self.run_bot(bot))
            task.add_done_callback(self.tasks.discard)
            self.tasks.add(task)

    async def shutdown(self) -> None:
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(
            *(asyncio.wait_for(task, timeout=10) for task in self.tasks),
            return_exceptions=True,
        )

    async def run_bot(self, bot_info: BotInfo) -> None:
        bot = Bot(self, bot_info.id, bot_info)
        task = asyncio.create_task(self._check_mailbox(bot))
        task.add_done_callback(self.tasks.discard)
        self.tasks.add(task)

    async def _check_mailbox(self, bot: Bot) -> None:
        bot_info = bot.bot_info
        imap_info = bot_info.imap
        if imap_info.tls:
            bot.imap_client = aioimaplib.IMAP4_SSL(
                host=imap_info.host, port=imap_info.port
            )
        else:
            bot.imap_client = aioimaplib.IMAP4(host=imap_info.host, port=imap_info.port)
        # login bot imap client
        if not await bot.login():
            return
        while True:
            try:
                # connect the bot instance
                self.bot_connect(bot)
                log(
                    "INFO",
                    f"<y>Bot {escape_tag(bot.self_id)}</y> connected",
                )
                # select the mailbox
                if not await bot.select_mailbox():
                    continue
                # infinite loop to check for new mails
                while True:
                    try:
                        await self._fetch_new_mail(bot)
                    except Exception as e:
                        log(
                            "ERROR",
                            (
                                f"<y>Bot {escape_tag(bot.self_id)}</y> "
                                "<r><bg #f8bbd0>"
                                "error while fetching mail"
                                "</bg #f8bbd0></r>"
                            ),
                            e,
                        )
                    await asyncio.sleep(CHECK_MAIL_INTERVAL)
            except Exception as e:
                log(
                    "ERROR",
                    (
                        f"<y>Bot {escape_tag(bot.self_id)}</y> "
                        "<r><bg #f8bbd0>"
                        f"error while setting up ({e}), reconnecting..."
                        "</bg #f8bbd0></r>"
                    ),
                    e,
                )
            finally:
                # logout bot imap client
                await bot.logout()
                # disconnect the bot instance
                if bot.self_id in self.bots:
                    self.bot_disconnect(bot)
            await asyncio.sleep(RECONNECT_INTERVAL)

    async def _fetch_new_mail(self, bot: Bot):
        if bot.mailbox != "INBOX":
            log(
                "TRACE",
                f"<y>Bot {escape_tag(bot.self_id)}</y> is not in INBOX, skip fetching",
            )
            return
        if bot.readonly:
            log(
                "TRACE",
                f"<y>Bot {escape_tag(bot.self_id)}</y> is readonly, skip fetching",
            )
            return
        # Search for unseen mails
        mails = await bot.fetch_unseen_mail_list()
        for mail in mails:
            # Handle the mail as an event
            event = NewMailMessageEvent(**model_dump(mail))
            task = asyncio.create_task(bot.handle_event(event))
            task.add_done_callback(self.tasks.discard)
            self.tasks.add(task)

    @override
    async def _call_api(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, bot: Bot, api: str, **data: Any
    ) -> Any:
        log("DEBUG", (f"Bot {bot.bot_info.id} calling API <y>{api}</y>"))
        api_handler = getattr(bot.__class__, api, None)
        if api_handler is None:
            raise ApiNotAvailable
        return await api_handler(bot, **data)

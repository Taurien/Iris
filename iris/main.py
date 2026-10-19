import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import ptt_daemon, telegram_bot, web  # noqa: E402  (must load after load_dotenv)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("iris.main")


async def run() -> None:
    app = telegram_bot.build_app()
    ptt_server = await ptt_daemon.start_ptt_server()
    web_runner = await web.start_web_server()

    try:
        async with app:
            await app.start()
            await app.updater.start_polling()
            log.info("iris up: telegram bot + ptt socket + web api")
            async with ptt_server:
                await ptt_server.serve_forever()
    finally:
        await web_runner.cleanup()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

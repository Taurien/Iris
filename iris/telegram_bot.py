import asyncio
import logging
import os
import tempfile
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from . import allowlist, audio_convert, brain, status, voice_stt, voice_tts

log = logging.getLogger("iris.telegram")


async def _reply_denied_silently(user_id: int) -> None:
    log.warning("rejected message from unauthorized telegram user_id=%s", user_id)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not allowlist.is_allowed(user.id):
        if user is not None:
            await _reply_denied_silently(user.id)
        return

    text = update.message.text or ""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply, backend = await brain.ask(text, "Telegram")
    await update.message.reply_text(reply)
    if backend is None:
        await asyncio.sleep(status.ERROR_MIN_VISIBLE_SEC)
    status.set_state("idle")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not allowlist.is_allowed(user.id):
        if user is not None:
            await _reply_denied_silently(user.id)
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.RECORD_VOICE)

    voice = update.message.voice
    tg_file = await context.bot.get_file(voice.file_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        ogg_in = Path(tmpdir) / "in.ogg"
        wav_in = Path(tmpdir) / "in.wav"
        wav_out = Path(tmpdir) / "reply.wav"
        ogg_out = Path(tmpdir) / "reply.ogg"

        await tg_file.download_to_drive(custom_path=str(ogg_in))
        audio_convert.ogg_to_wav(ogg_in, wav_in)

        text, lang = await asyncio.to_thread(voice_stt.transcribe, wav_in)
        reply, backend = await brain.ask(text, "Voz (Telegram)")

        status.set_state("speaking", "Voz (Telegram)", detail=backend)
        await asyncio.to_thread(voice_tts.synthesize, reply, wav_out, lang)
        audio_convert.wav_to_ogg_opus(wav_out, ogg_out)

        with open(ogg_out, "rb") as f:
            await update.message.reply_voice(voice=f)
        if backend is None:
            await asyncio.sleep(status.ERROR_MIN_VISIBLE_SEC)
        status.set_state("idle")


def build_app() -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    return app

"""Voice-note transcription with OpenAI Whisper."""

from __future__ import annotations

import os
from pathlib import Path

from openai import AsyncOpenAI


class WhisperClient:
    def __init__(self, api_key: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    async def transcribe(self, audio_path: str | Path, language: str | None = None) -> str:
        """Transcribe an audio file (.ogg, .opus, .mp3, .wav).

        Telegram sends voice notes as .oga (Opus). Leave `language` empty to auto-detect,
        or pin it (e.g. "en", "ru") for better accuracy on short notes.
        """
        path = Path(audio_path)
        kwargs = {"language": language} if language else {}
        with path.open("rb") as f:
            transcript = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=(path.name, f, "audio/ogg"),
                **kwargs,
            )
        return transcript.text.strip()

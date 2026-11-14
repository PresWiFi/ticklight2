"""Speech to text through any OpenAI-compatible /audio/transcriptions endpoint,
e.g. Speaches (faster-whisper) on the Proxmox GPU, or OpenAI/Groq."""

from __future__ import annotations

import logging

import httpx

from .config import Settings

log = logging.getLogger("ticklight.voice")


class VoiceUnavailable(Exception):
    pass


class Voice:
    def __init__(self, s: Settings):
        self.s = s

    @property
    def configured(self) -> bool:
        return bool(self.s.whisper_url)

    async def transcribe(self, audio: bytes, filename: str, mime: str) -> str:
        if not self.configured:
            raise VoiceUnavailable("no speech-to-text server configured")
        if len(audio) > 25 * 1024 * 1024:
            raise VoiceUnavailable("recording too long")
        data = {"model": self.s.whisper_model, "response_format": "json"}
        if self.s.whisper_language:
            data["language"] = self.s.whisper_language
        headers = {"Authorization": f"Bearer {self.s.whisper_key}"} if self.s.whisper_key else {}
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(f"{self.s.whisper_url}/audio/transcriptions", headers=headers,
                                 data=data, files={"file": (filename, audio, mime)})
                r.raise_for_status()
                return (r.json().get("text") or "").strip()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("transcription failed: %s", e)
            raise VoiceUnavailable("speech-to-text server didn't answer") from e

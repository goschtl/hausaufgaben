"""Aussprache einer Aufnahme von einem OpenAI-Audiomodell bewerten lassen (über pydantic-ai).

Braucht OPENAI_API_KEY (z. B. in .env) und ffmpeg, weil OpenAI nur WAV/MP3 annimmt,
der Browser aber WebM/MP4 aufnimmt. Modell änderbar über HA_AUSSPRACHE_MODELL.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models.openai import OpenAIChatModel

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
MODELL = os.environ.get("HA_AUSSPRACHE_MODELL", "gpt-audio-1.5")

ANWEISUNG = """\
Du bist ein freundlicher Englischlehrer für ein Kind in der 7. Klasse in Bayern.
Du hörst eine kurze Aufnahme, in der das Kind ein englisches Wort oder eine Wendung
aus seiner Vokabelliste spricht. Beurteile, ob es das RICHTIGE Wort ist und ob es
verständlich und im Wesentlichen korrekt ausgesprochen ist (britisches oder
amerikanisches Englisch ist beides in Ordnung, ein deutscher Akzent auch).
Achte besonders auf typische Fehler deutscher Kinder: stumme Buchstaben (knit, choir),
falsche Betonung (orchestra, culture), th, w/v, deutsch ausgesprochene Vokale.
Der Tipp ist auf Deutsch, höchstens zwei kurze Sätze, ermutigend, konkret zum Laut
(z. B. „Das k in knit ist stumm – sag einfach nit.“). Wenn alles passt, lobe kurz.
"""


class Bewertung(BaseModel):
    richtig: bool = Field(description="Richtiges Wort und verständlich ausgesprochen")
    note: int = Field(ge=1, le=3, description="3 = sehr gut, 2 = verständlich mit kleinem Fehler, 1 = falsch/unverständlich")
    gehoert: str = Field(description="Was du gehört hast, in Lautschrift oder als Wort")
    tipp: str = Field(description="Kurzer Tipp auf Deutsch an das Kind")


_agent: Agent[None, Bewertung] | None = None


def verfuegbar() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def agent() -> Agent[None, Bewertung]:
    global _agent
    if _agent is None:
        _agent = Agent(OpenAIChatModel(MODELL), output_type=Bewertung, instructions=ANWEISUNG, retries={"output": 3})
    return _agent


def als_wav(audio: Path) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(audio), "-af", "adelay=300:all=1,apad=pad_dur=0.3", "-ac", "1", "-ar", "16000", "-f", "wav", "-"],
        check=True, capture_output=True, timeout=30,
    ).stdout


def bewerten(audio: Path, englisch: str, deutsch: str) -> Bewertung:
    prompt = f"Gesuchtes Wort laut Vokabelliste: „{englisch}“ (deutsch: „{deutsch}“). Bewerte die Aufnahme."
    result = agent().run_sync([prompt, BinaryContent(data=als_wav(audio), media_type="audio/wav")])
    return result.output

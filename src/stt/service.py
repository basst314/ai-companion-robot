"""Speech-to-text service interface and mock streaming implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from shared.models import Language, Transcript


class SttService(Protocol):
    """Interface for streaming transcript updates."""

    async def stream_transcripts(self) -> AsyncIterator[Transcript]:
        """Yield partial and final transcript results."""


@dataclass(slots=True)
class MockSttService:
    """Deterministic transcript stream for tests and early development."""

    utterances: tuple[str, ...] = ()
    emit_partials: bool = True
    language: Language = Language.ENGLISH
    confidence: float = 0.98
    _sequences: tuple[tuple[Transcript, ...], ...] = field(default_factory=tuple)

    async def stream_transcripts(self) -> AsyncIterator[Transcript]:
        if self._sequences:
            for sequence in self._sequences:
                for transcript in sequence:
                    yield transcript
            return

        for utterance in self.utterances:
            for transcript in self._build_sequence(utterance):
                yield transcript

    def _build_sequence(self, utterance: str) -> Iterable[Transcript]:
        started_at = datetime.now(UTC)
        if self.emit_partials:
            words = utterance.split()
            if len(words) > 1:
                yield Transcript(
                    text=" ".join(words[:-1]),
                    language=self.language,
                    confidence=self.confidence,
                    is_final=False,
                    started_at=started_at,
                )

        yield Transcript(
            text=utterance,
            language=self.language,
            confidence=self.confidence,
            is_final=True,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )

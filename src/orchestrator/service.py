"""Orchestrator service implementation for the mock end-to-end runtime."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai.cloud import CloudAiService
from ai.local import LocalAiService
from hardware.service import HardwareService
from memory.service import MemoryService
from orchestrator.router import IntentRouter
from orchestrator.state import LifecycleStage, OrchestratorState
from shared.config import AppConfig
from shared.console import ConsoleFormatter
from shared.events import Event, EventName
from shared.models import (
    ActionRequest,
    AiResponse,
    ComponentName,
    EmotionState,
    InteractionContext,
    InteractionRecord,
    QueryResult,
    RobotStateSnapshot,
    RouteDecision,
    RouteKind,
    Transcript,
)
from stt.service import SttService
from tts.service import TtsService
from ui.service import UiService
from vision.service import VisionService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OrchestratorService:
    """Central coordinator for routed interactions across local capabilities."""

    config: AppConfig
    state: OrchestratorState
    router: IntentRouter
    memory: MemoryService
    vision: VisionService
    ui: UiService
    hardware: HardwareService
    local_ai: LocalAiService
    cloud_ai: CloudAiService
    stt: SttService | None
    tts: TtsService
    event_history: list[Event] = field(default_factory=list)

    async def start(self) -> None:
        """Prepare startup state for the local runtime."""

        await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)

    async def stop(self) -> None:
        """Prepare shutdown wiring for the local runtime."""

        await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)

    async def run(self) -> None:
        """Run the manual or speech-driven end-to-end interaction loop."""

        await self.start()
        await self.handle_event(
            Event(
                name=EventName.LISTENING_STARTED,
                source=ComponentName.ORCHESTRATOR,
            )
        )

        if self.config.runtime.input_mode == "speech":
            await self._run_speech_loop()
        elif self.config.runtime.interactive_console:
            while True:
                try:
                    raw_text = await asyncio.to_thread(input, "You> ")
                except (EOFError, KeyboardInterrupt):
                    logger.info("interactive console closed; stopping orchestrator loop")
                    break

                if raw_text.strip().lower() in {"quit", "exit"}:
                    break
                await self._run_manual_input(raw_text)
        else:
            for raw_text in self.config.runtime.manual_inputs:
                await self._run_manual_input(raw_text)

        await self.stop()

    async def _run_speech_loop(self) -> None:
        """Capture one utterance at a time through the configured STT service."""

        if self.stt is None:
            raise RuntimeError("speech input mode requires an STT service")

        if self.config.runtime.interactive_console:
            while True:
                try:
                    command = await asyncio.to_thread(
                        input,
                        "Press Enter to start listening, type a phrase to use it directly, or type 'exit' to quit> ",
                    )
                except (EOFError, KeyboardInterrupt):
                    logger.info("interactive speech console closed; stopping orchestrator loop")
                    break

                text = command.strip()
                if text.lower() in {"quit", "exit"}:
                    break
                if text:
                    await self._run_manual_input(text)
                    continue
                await self._run_stt_turn()
            return

        utterance_count = max(1, len(self.config.runtime.manual_inputs))
        for _ in range(utterance_count):
            await self._run_stt_turn()

    async def _run_stt_turn(self) -> None:
        """Capture, transcribe, and execute one speech turn."""

        if self.stt is None:
            raise RuntimeError("speech input mode requires an STT service")

        await self._set_lifecycle(LifecycleStage.LISTENING, EmotionState.LISTENING)
        try:
            final_transcript: Transcript | None = None
            async for transcript in self.stt.stream_transcripts():
                if transcript.is_final:
                    final_transcript = transcript
                    self._print_transcript_preview(transcript, is_final=True)
                    break
                await self.handle_partial_transcript(transcript)
                self._print_transcript_preview(transcript, is_final=False)
            if final_transcript is None:
                raise RuntimeError("STT stream completed without a final transcript")
        except Exception as exc:
            logger.exception("stt failed")
            self.state.last_error = str(exc)
            await self.handle_event(
                Event(
                    name=EventName.ERROR_OCCURRED,
                    source=ComponentName.STT,
                    payload={"error": str(exc)},
                )
            )
            await self._set_lifecycle(LifecycleStage.ERROR, EmotionState.CURIOUS)
            await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)
            return

        transcript = final_transcript
        if not transcript.text.strip():
            self.state.current_transcript = transcript
            self.state.active_language = transcript.language
            await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)
            return

        await self.run_turn(transcript)

    async def handle_partial_transcript(self, transcript: Transcript) -> None:
        """Accept a partial transcript and update listening state only."""

        self._debug_transcript(transcript, kind="partial")
        self.state.current_transcript = transcript
        self.state.active_language = transcript.language
        preview_text = None if self.config.runtime.interactive_console else transcript.text
        await self._set_lifecycle(LifecycleStage.LISTENING, EmotionState.LISTENING, preview_text)
        await self.handle_event(
            Event(
                name=EventName.TRANSCRIPT_PARTIAL,
                source=ComponentName.STT,
                payload={"transcript": transcript},
            )
        )

    async def run_turn(self, transcript: Transcript) -> None:
        """Process a final transcript through routing, execution, and response."""

        self._debug_transcript(transcript, kind="final")
        self.state.interaction_id += 1
        self.state.current_transcript = transcript
        self.state.active_language = transcript.language
        await self._set_lifecycle(LifecycleStage.PROCESSING, EmotionState.THINKING, transcript.text)
        await self.handle_event(
            Event(
                name=EventName.TRANSCRIPT_FINAL,
                source=ComponentName.STT,
                payload={"transcript": transcript},
            )
        )

        context = await self._build_context()
        decision = await self.router.route(transcript, context)
        self.state.last_route = decision
        await self.handle_event(
            Event(
                name=EventName.ROUTE_SELECTED,
                source=ComponentName.ORCHESTRATOR,
                payload={"decision": decision},
            )
        )

        await self.execute_route(decision, context)

    async def execute_route(self, decision: RouteDecision, context: InteractionContext) -> None:
        """Execute the selected route and deliver the response."""

        transcript = self.state.current_transcript
        if transcript is None:
            raise RuntimeError("no active transcript available for route execution")

        try:
            if decision.kind is RouteKind.LOCAL_ACTION:
                result = await self.hardware.execute_action(
                    ActionRequest(name=decision.action_name or "unknown_action", arguments=decision.arguments)
                )
                await self.handle_event(
                    Event(
                        name=EventName.ACTION_EXECUTED,
                        source=ComponentName.HARDWARE,
                        payload={"result": result},
                    )
                )
                self._apply_state_changes(result.state_changes)
                response = AiResponse(
                    text=result.message,
                    emotion=EmotionState.HAPPY if result.success else EmotionState.CURIOUS,
                    intent=decision.action_name,
                )
            elif decision.kind is RouteKind.LOCAL_QUERY:
                result = await self._run_local_query(decision, context)
                await self.handle_event(
                    Event(
                        name=EventName.QUERY_EXECUTED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": result},
                    )
                )
                response = AiResponse(
                    text=result.answer_text,
                    emotion=EmotionState.CURIOUS,
                    intent=decision.query_name,
                )
            elif decision.kind is RouteKind.LOCAL_LLM:
                response = await self.local_ai.generate_reply(transcript, context)
            else:
                response = await self._run_cloud_with_fallback(transcript, context)
        except Exception as exc:
            logger.exception("route execution failed")
            self.state.last_error = str(exc)
            await self.handle_event(
                Event(
                    name=EventName.ERROR_OCCURRED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"error": str(exc)},
                )
            )
            response = AiResponse(
                text="I hit a problem, but I am still here and ready for the next turn.",
                emotion=EmotionState.CURIOUS,
                intent="error_recovery",
            )

        self.state.current_response = response.text
        await self.handle_event(
            Event(
                name=EventName.RESPONSE_READY,
                source=ComponentName.ORCHESTRATOR,
                payload={"response": response},
            )
        )
        await self._deliver_response(response, transcript, decision.kind)

    async def handle_event(self, event: Event) -> None:
        """Accept an event for routing history and state transitions."""

        self.event_history.append(event)
        self.state.last_event_name = event.name.value
        logger.info("event=%s source=%s", event.name.value, event.source.value)

    async def _build_context(self) -> InteractionContext:
        active_user = await self.memory.get_active_user()
        detections = await self._safe_get_detections()
        history = await self.memory.get_recent_history()
        self.state.active_user_id = active_user.user_id if active_user else None
        self.state.last_detections = detections
        return InteractionContext(
            active_user=active_user,
            recent_history=history,
            current_detections=detections,
            robot_state=RobotStateSnapshot(
                lifecycle=self.state.lifecycle.value,
                emotion=self.state.emotion,
                eyes_open=self.state.eyes_open,
                head_direction=self.state.head_direction,
            ),
        )

    async def _run_local_query(
        self,
        decision: RouteDecision,
        context: InteractionContext,
    ) -> QueryResult:
        if decision.query_name == "visible_people":
            if not context.current_detections:
                return QueryResult(answer_text="I do not see anyone right now.")

            names = ", ".join(detection.label for detection in context.current_detections)
            return QueryResult(answer_text=f"I can currently see {names}.")

        if decision.query_name == "user_summary":
            summary = await self.memory.get_user_summary(self.state.active_user_id)
            return QueryResult(answer_text=summary)

        if decision.query_name == "robot_status":
            return QueryResult(
                answer_text=(
                    f"I am {self.state.lifecycle.value}, my eyes are "
                    f"{'open' if self.state.eyes_open else 'closed'}, "
                    f"and my head is pointing {self.state.head_direction}."
                )
            )

        return QueryResult(answer_text="I do not have an answer for that local query yet.")

    async def _run_cloud_with_fallback(
        self,
        transcript: Transcript,
        context: InteractionContext,
    ) -> AiResponse:
        try:
            return await self.cloud_ai.generate_reply(transcript, context)
        except Exception as exc:
            logger.exception("cloud AI failed")
            self.state.last_error = str(exc)
            await self.handle_event(
                Event(
                    name=EventName.ERROR_OCCURRED,
                    source=ComponentName.CLOUD,
                    payload={"error": str(exc)},
                )
            )
            return AiResponse(
                text="My cloud brain is unavailable, so I am falling back to a simple local reply.",
                emotion=EmotionState.CURIOUS,
                intent="cloud_fallback",
            )

    async def _deliver_response(
        self,
        response: AiResponse,
        transcript: Transcript,
        route_kind: RouteKind,
    ) -> None:
        await self._set_lifecycle(LifecycleStage.RESPONDING, response.emotion, response.text)
        await self.ui.show_text(response.text)

        try:
            await self.handle_event(
                Event(
                    name=EventName.TTS_STARTED,
                    source=ComponentName.TTS,
                    payload={"text": response.text},
                )
            )
            await self.tts.speak(response.text)
            await self.handle_event(
                Event(
                    name=EventName.TTS_FINISHED,
                    source=ComponentName.TTS,
                    payload={"text": response.text},
                )
            )
        except Exception as exc:
            logger.exception("tts failed")
            self.state.last_error = str(exc)
            await self.handle_event(
                Event(
                    name=EventName.ERROR_OCCURRED,
                    source=ComponentName.TTS,
                    payload={"error": str(exc)},
                )
            )

        await self.memory.save_interaction(
            InteractionRecord(
                user_text=transcript.text,
                assistant_text=response.text,
                language=transcript.language,
                timestamp=datetime.now(UTC),
                route_kind=route_kind,
                user_id=self.state.active_user_id,
            )
        )
        await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)

    async def _run_manual_input(self, raw_text: str) -> None:
        text = raw_text.strip()
        if not text:
            return

        transcript = Transcript(
            text=text,
            language=self.config.default_language,
            confidence=1.0,
            is_final=True,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )
        await self.run_turn(transcript)

    async def _safe_get_detections(self) -> tuple:
        try:
            detections = await self.vision.get_current_detections()
            if detections:
                await self.handle_event(
                    Event(
                        name=EventName.FACE_DETECTED,
                        source=ComponentName.VISION,
                        payload={"detections": detections},
                    )
                )
            return detections
        except Exception as exc:
            logger.exception("vision lookup failed")
            self.state.last_error = str(exc)
            await self.handle_event(
                Event(
                    name=EventName.ERROR_OCCURRED,
                    source=ComponentName.VISION,
                    payload={"error": str(exc)},
                )
            )
            return ()

    async def _set_lifecycle(
        self,
        lifecycle: LifecycleStage,
        emotion: EmotionState,
        preview_text: str | None = None,
    ) -> None:
        self.state.lifecycle = lifecycle
        self.state.emotion = emotion
        await self.ui.render_state(lifecycle.value, emotion.value, preview_text)

    def _apply_state_changes(self, state_changes: dict[str, object]) -> None:
        if "eyes_open" in state_changes:
            self.state.eyes_open = bool(state_changes["eyes_open"])
        if "head_direction" in state_changes:
            self.state.head_direction = str(state_changes["head_direction"])

    def _debug_transcript(self, transcript: Transcript, kind: str) -> None:
        """Print a concise transcript debug line for local development."""

        if self.config.runtime.interactive_console:
            return

        formatter = ConsoleFormatter()
        line = (
            "[STT] "
            f"{kind} "
            f"language={transcript.language.value} "
            f"confidence={transcript.confidence:.2f} "
            f"text={transcript.text!r}"
        )
        formatter.emit(
            formatter.stamp(f"{formatter.stt_label('[STT]')} {formatter.transcript(line.removeprefix('[STT] '))}"),
            plain_text=formatter.stamp(line),
        )

    def _print_transcript_preview(self, transcript: Transcript, *, is_final: bool) -> None:
        if not self.config.runtime.interactive_console:
            return

        formatter = ConsoleFormatter()
        label = "Final transcript" if is_final else "Listening"
        language = transcript.language.value
        plain_message = formatter.stamp(f"{label} [{language}]: {transcript.text or '...'}")
        message = formatter.stamp(
            f"{formatter.label(f'{label} [{language}]:')} {formatter.transcript(transcript.text or '...')}"
        )
        if is_final:
            formatter.emit(
                f"\r{message}".ljust(120),
                plain_text=plain_message,
            )
            return

        formatter.emit(
            f"\r{message}".ljust(120),
            plain_text=plain_message,
            end="",
            flush=True,
        )

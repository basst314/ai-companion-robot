"""Orchestrator service implementation for the mock end-to-end runtime."""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import logging
import os
import select
import sys
import termios
from pathlib import Path
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai.cloud import CloudResponseService, CloudToolRequest, CloudToolResult
from ai.realtime import RealtimeConversationService, RealtimeToolCall, RealtimeToolResult
from hardware.service import HardwareService
from memory.service import MemoryService
from orchestrator.capabilities import CapabilityRegistry
from orchestrator.reactive import ReactivePolicyEngine
from orchestrator.router import TurnDirector
from orchestrator.state import LifecycleStage, OrchestratorState
from shared.config import AppConfig
from shared.console import ConsoleFormatter, TerminalDebugSink, configure_terminal_debug_screen
from shared.events import Event, EventBus, EventName
from shared.models import (
    ActionRequest,
    AiResponse,
    ComponentName,
    EmotionState,
    InteractionContext,
    InteractionRecord,
    Language,
    PlanStep,
    PlanStepResult,
    QueryResult,
    RobotStateSnapshot,
    RouteKind,
    SpeechJobStatus,
    SpeechRequest,
    StepPhase,
    Transcript,
    TurnPlan,
)
from stt.service import SttService, WakeWordService, strip_wake_phrase
from stt.service import SharedLiveSpeechState, WakeDetectionResult
from tts.service import TtsService
from ui.service import UiService
from vision.service import VisionService

logger = logging.getLogger(__name__)
_OPENAI_RESPONSE_RESUME_WINDOW_SECONDS = 5 * 60


def _payload_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


@dataclass(slots=True, frozen=True)
class SpeechTurnOutcome:
    """Outcome flags used to decide whether the next wake-free turn should open."""

    transcript_empty: bool = False
    follow_up_eligible: bool = False


@dataclass(slots=True)
class OrchestratorService:
    """Central coordinator for routed interactions across local capabilities."""

    config: AppConfig
    state: OrchestratorState
    turn_director: TurnDirector
    capability_registry: CapabilityRegistry
    reactive_policy: ReactivePolicyEngine
    event_bus: EventBus
    memory: MemoryService
    vision: VisionService
    ui: UiService
    hardware: HardwareService
    cloud_response: CloudResponseService
    stt: SttService | None
    tts: TtsService
    wake_word: WakeWordService | None = None
    realtime_conversation: RealtimeConversationService | None = None
    shared_live_speech_state: SharedLiveSpeechState | None = None
    terminal_debug: TerminalDebugSink | None = None
    event_history: list[Event] = field(default_factory=list)
    _active_speech_trigger: str | None = field(default=None, init=False, repr=False)
    _last_wake_detection: WakeDetectionResult | None = field(default=None, init=False, repr=False)
    _turn_latency_marks: dict[str, datetime] = field(default_factory=dict, init=False, repr=False)

    async def start(self) -> None:
        """Prepare startup state for the local runtime."""

        if self.terminal_debug is not None:
            configure_terminal_debug_screen(self.terminal_debug)
            self.terminal_debug.activate()
        if hasattr(self.ui, "start"):
            await self.ui.start()
        if self.stt is not None and hasattr(self.stt, "start"):
            await self.stt.start()
        if hasattr(self.cloud_response, "start"):
            await self.cloud_response.start()
        if self.realtime_conversation is not None:
            await self.realtime_conversation.start()
        if self.config.tts.backend != "mock" and hasattr(self.tts, "start"):
            await self.tts.start()
        await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)

    async def stop(self) -> None:
        """Prepare shutdown wiring for the local runtime."""

        await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)
        if self.stt is not None and hasattr(self.stt, "shutdown"):
            await self.stt.shutdown()
        elif self.wake_word is not None and hasattr(self.wake_word, "shutdown"):
            await self.wake_word.shutdown()
        elif self.shared_live_speech_state is not None:
            await self.shared_live_speech_state.close()
        if hasattr(self.cloud_response, "shutdown"):
            await self.cloud_response.shutdown()
        if self.realtime_conversation is not None:
            await self.realtime_conversation.shutdown()
        if hasattr(self.tts, "shutdown"):
            await self.tts.shutdown()
        if hasattr(self.ui, "shutdown"):
            await self.ui.shutdown()
        if self.terminal_debug is not None:
            self.terminal_debug.close()
            configure_terminal_debug_screen(None)

    async def run(self) -> None:
        """Run the manual or speech-driven end-to-end interaction loop."""

        await self.start()
        try:
            if self.config.runtime.interaction_backend == "openai_realtime":
                await self._run_realtime_speech_loop()
            elif self.config.runtime.input_mode == "speech":
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
        finally:
            await self.stop()

    async def _run_speech_loop(self) -> None:
        """Capture one utterance at a time through the configured STT service."""

        if self.stt is None:
            raise RuntimeError("speech input mode requires an STT service")

        if self.config.runtime.interactive_console:
            await self._run_interactive_speech_loop()
            return

        remaining_turn_budget = max(1, len(self.config.runtime.manual_inputs)) if self.config.runtime.stt_backend == "mock" else None
        while remaining_turn_budget is None or remaining_turn_budget > 0:
            wake_detected = await self._await_wake_word()
            if not wake_detected:
                continue
            remaining_turn_budget = await self._run_follow_up_session(remaining_turn_budget=remaining_turn_budget)
            if remaining_turn_budget == 0:
                return

    async def _run_interactive_speech_loop(self) -> None:
        """Allow keyboard input and wake-word activation to coexist in interactive mode."""

        self._show_interactive_speech_hint()

        isatty = getattr(sys.stdin, "isatty", None)
        if not isatty or not isatty():
            await self._run_interactive_speech_loop_non_tty()
            return

        wake_task: asyncio.Task[bool] | None = None
        input_buffer = ""
        with _stdin_cbreak_mode():
            while True:
                if not input_buffer and wake_task is None and self.wake_word is not None:
                    wake_task = asyncio.create_task(self._await_wake_word())

                char = await asyncio.to_thread(_read_console_char_ready, 0.1)
                if char is not None:
                    if wake_task is not None and not wake_task.done():
                        wake_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await wake_task
                        wake_task = None

                    if char in {"\r", "\n"}:
                        text = input_buffer.strip()
                        input_buffer = ""
                        self._clear_typed_input_preview()
                        if text.lower() in {"quit", "exit"}:
                            break
                        if text:
                            await self._run_manual_input(text)
                        else:
                            self._clear_wake_handoff()
                            self._begin_manual_utterance()
                            self._mark_manual_listening_awake()
                            await self._run_follow_up_session()
                        self._show_interactive_speech_hint()
                        continue

                    if char in {"\x7f", "\b"}:
                        input_buffer = input_buffer[:-1]
                    elif char == "\x03":
                        raise KeyboardInterrupt()
                    elif char.isprintable():
                        input_buffer += char
                    self._show_typed_input_preview(input_buffer)
                    continue

                if wake_task is not None and wake_task.done():
                    wake_detected = await wake_task
                    wake_task = None
                    if wake_detected:
                        await self._run_follow_up_session()
                        self._show_interactive_speech_hint()

    async def _run_interactive_speech_loop_non_tty(self) -> None:
        """Preserve test and redirected-stdin behavior without TTY polling."""

        while True:
            input_task = asyncio.create_task(asyncio.to_thread(_read_console_line_ready, 0.1))
            tasks: set[asyncio.Task[object]] = {input_task}
            wake_task: asyncio.Task[bool] | None = None
            if self.wake_word is not None:
                wake_task = asyncio.create_task(self._await_wake_word())
                tasks.add(wake_task)

            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            if wake_task is not None and wake_task in done:
                wake_detected = await wake_task
                if wake_detected:
                    if not input_task.done():
                        input_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await input_task
                    await self._run_follow_up_session()
                    self._show_interactive_speech_hint()
                    continue
                if not input_task.done():
                    input_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await input_task

            if input_task in done:
                if wake_task is not None and not wake_task.done():
                    wake_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await wake_task
                command = input_task.result()
                text = command.strip() if command is not None else ""
                if text.lower() in {"quit", "exit"}:
                    break
                if text:
                    await self._run_manual_input(text)
                else:
                    self._clear_wake_handoff()
                    self._begin_manual_utterance()
                    self._mark_manual_listening_awake()
                    await self._run_follow_up_session()
                self._show_interactive_speech_hint()

    async def _await_wake_word(self) -> bool:
        """Wait for the configured wake word before starting a speech turn."""

        if self.wake_word is None:
            self._start_turn_latency_window()
            return True
        detection = await self.wake_word.wait_for_wake_word()
        if not detection.detected:
            return False
        self._start_turn_latency_window()
        logger.info(
            "turn_trace wake_word_handled trigger=wake phrase=%s",
            self.config.runtime.wake_word_phrase,
        )
        self._active_speech_trigger = "wake"
        self._last_wake_detection = detection
        if self.stt is not None and hasattr(self.stt, "begin_utterance"):
            self.stt.begin_utterance(trigger="wake", detection=detection)
        return True

    async def _run_realtime_speech_loop(self) -> None:
        """Run wake-triggered OpenAI Realtime speech sessions."""

        if self.realtime_conversation is None:
            raise RuntimeError("openai_realtime interaction backend requires a realtime conversation service")
        if self.shared_live_speech_state is None:
            raise RuntimeError("openai_realtime interaction backend requires shared live speech state")
        while True:
            wake_detected = await self._await_wake_word()
            if not wake_detected:
                continue
            await self._run_realtime_awake_session()

    async def _run_realtime_awake_session(self) -> None:
        """Stream the active microphone session to the realtime backend after wake."""

        if self.realtime_conversation is None or self.shared_live_speech_state is None:
            raise RuntimeError("realtime session requested without realtime dependencies")

        shared_state = self.shared_live_speech_state
        await self._set_lifecycle(LifecycleStage.LISTENING, EmotionState.LISTENING)
        self._update_realtime_debug_session_started()
        await self.handle_event(
            Event(
                name=EventName.LISTENING_STARTED,
                source=ComponentName.ORCHESTRATOR,
                payload={"trigger": self._active_speech_trigger or "wake"},
            )
        )
        await self._apply_reactive_steps(
            self.reactive_policy.listening_started(
                self.state,
                has_attention_target=bool(self.state.last_detections or self.config.mocks.visible_people),
            )
        )

        audio_chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def enqueue_chunk(chunk: bytes, _chunk_start_offset: int) -> None:
            if chunk:
                loop.call_soon_threadsafe(audio_chunks.put_nowait, chunk)

        await shared_state.ensure_session()
        await shared_state.sync()
        detection = self._last_wake_detection
        self._last_wake_detection = None
        if detection is not None and detection.utterance_stream_start_offset is not None:
            shared_state.start_utterance(stream_start_offset=detection.utterance_stream_start_offset)
        elif detection is not None and detection.audio_window is not None:
            shared_state.start_utterance(initial_window=detection.audio_window)
        else:
            shared_state.start_utterance()

        initial_window = shared_state.current_utterance_window(
            threshold=0,
            source_path=Path("shared-live-realtime-session.wav"),
        )
        if initial_window is not None and initial_window.pcm_data:
            audio_chunks.put_nowait(initial_window.pcm_data)

        shared_state.add_chunk_listener(enqueue_chunk)
        try:
            await self.realtime_conversation.run_awake_session(audio_chunks=audio_chunks)
        except Exception as exc:
            logger.exception("realtime session failed")
            self.state.last_error = str(exc)
            await self.handle_event(
                Event(
                    name=EventName.ERROR_OCCURRED,
                    source=ComponentName.CLOUD,
                    payload={"error": str(exc)},
                )
            )
            await self._set_lifecycle(LifecycleStage.ERROR, EmotionState.CURIOUS)
        finally:
            shared_state.remove_chunk_listener(enqueue_chunk)
            shared_state.reset_utterance()
            audio_chunks.put_nowait(None)
            self._active_speech_trigger = None
            self.state.response_emotion = None
            await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)

    def _update_realtime_debug_session_started(self) -> None:
        if self.terminal_debug is None:
            return
        update_realtime_status = getattr(self.terminal_debug, "update_realtime_status", None)
        if not callable(update_realtime_status):
            return
        voice = self.realtime_conversation.voice if self.realtime_conversation is not None else None
        update_realtime_status(
            phase="listening",
            voice=voice,
            input_audio_bytes=0,
            input_audio_chunks=0,
            output_audio_bytes=0,
            output_audio_chunks=0,
            response_count=0,
            interrupt_count=0,
            last_event="session_started",
        )

    async def _run_stt_turn(self) -> SpeechTurnOutcome:
        """Capture, transcribe, and execute one speech turn."""

        if self.stt is None:
            raise RuntimeError("speech input mode requires an STT service")

        self.state.last_error = None
        await self._set_lifecycle(LifecycleStage.LISTENING, EmotionState.LISTENING)
        await self.handle_event(
            Event(
                name=EventName.LISTENING_STARTED,
                source=ComponentName.ORCHESTRATOR,
                payload={
                    "trigger": self._active_speech_trigger or "manual",
                },
            )
        )
        await self._apply_reactive_steps(
            self.reactive_policy.listening_started(
                self.state,
                has_attention_target=bool(self.state.last_detections or self.config.mocks.visible_people),
            )
        )
        strip_wake_phrase_from_turn = self._active_speech_trigger == "wake"
        try:
            final_transcript: Transcript | None = None
            async for transcript in self.stt.stream_transcripts():
                if strip_wake_phrase_from_turn:
                    transcript = self._strip_wake_phrase_from_transcript(transcript)
                if transcript.is_final:
                    final_transcript = transcript
                    vad_end_at = transcript.metadata.get("vad_end_at")
                    if isinstance(vad_end_at, datetime):
                        self._mark_turn_latency("vad_end", vad_end_at)
                    stt_final_ready_at = transcript.metadata.get("stt_final_ready_at")
                    if isinstance(stt_final_ready_at, datetime):
                        self._mark_turn_latency("stt_final_ready", stt_final_ready_at)
                    else:
                        self._mark_turn_latency("stt_final_ready")
                    self._log_turn_latency_span("wake_detected->vad_end", "wake_detected", "vad_end")
                    self._log_turn_latency_span("vad_end->stt_final_ready", "vad_end", "stt_final_ready")
                    logger.info(
                        "turn_trace final_transcript_ready text_len=%s trigger=%s",
                        len(transcript.text),
                        self._active_speech_trigger or "manual",
                    )
                    if transcript.text.strip():
                        self._show_transcript_update(transcript, is_final=True)
                    break
                await self.handle_partial_transcript(transcript)
                self._show_transcript_update(transcript, is_final=False)
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
            return SpeechTurnOutcome()
        finally:
            self._active_speech_trigger = None

        transcript = final_transcript
        if not transcript.text.strip():
            self.state.current_transcript = transcript
            self.state.active_language = transcript.language
            await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)
            return SpeechTurnOutcome(transcript_empty=True)

        follow_up_eligible = await self.run_turn(transcript)
        return SpeechTurnOutcome(follow_up_eligible=follow_up_eligible)

    async def _run_follow_up_session(self, *, remaining_turn_budget: int | None = None) -> int | None:
        """Run one active speech session, chaining wake-free follow-up turns when eligible."""

        remaining_follow_up_turns = self.config.runtime.follow_up_max_turns
        while True:
            outcome = await self._run_stt_turn()
            if remaining_turn_budget is not None:
                remaining_turn_budget -= 1
            if remaining_turn_budget == 0:
                return 0
            if not self._should_continue_follow_up_session(outcome):
                return remaining_turn_budget
            if remaining_follow_up_turns <= 0:
                return remaining_turn_budget
            remaining_follow_up_turns -= 1
            self._begin_follow_up_utterance()
            self._mark_manual_listening_awake()

    async def handle_partial_transcript(self, transcript: Transcript) -> None:
        """Accept a partial transcript and update listening state only."""

        self._debug_transcript(transcript, kind="partial")
        logger.info(
            "turn_trace partial_transcript_accepted text_len=%s trigger=%s",
            len(transcript.text),
            self._active_speech_trigger or "manual",
        )
        self.state.current_transcript = transcript
        self.state.active_language = transcript.language
        self.state.last_step_results = ()
        preview_text = None if self.config.runtime.interactive_console else transcript.text
        await self._set_lifecycle(LifecycleStage.LISTENING, EmotionState.LISTENING, preview_text)
        self._update_terminal_debug_transcript(transcript, is_final=False)
        await self.handle_event(
            Event(
                name=EventName.TRANSCRIPT_PARTIAL,
                source=ComponentName.STT,
                payload={"transcript": transcript},
            )
        )
        await self._apply_reactive_steps(self.reactive_policy.partial_transcript(self.state, transcript))

    async def run_turn(self, transcript: Transcript) -> bool:
        """Process a final transcript through local routing, execution, and response."""

        self._debug_transcript(transcript, kind="final")
        logger.info(
            "turn_trace final_transcript_accepted text_len=%s trigger=%s",
            len(transcript.text),
            self._active_speech_trigger or "manual",
        )
        self.state.last_error = None
        self.state.interaction_id += 1
        self.state.current_transcript = transcript
        self.state.active_language = transcript.language
        self.state.last_plan = None
        self.state.last_step_results = ()
        self._update_terminal_debug_transcript(transcript, is_final=True)
        await self._set_lifecycle(LifecycleStage.PROCESSING, EmotionState.THINKING, transcript.text)
        await self.handle_event(
            Event(
                name=EventName.TRANSCRIPT_FINAL,
                source=ComponentName.STT,
                payload={"transcript": transcript},
            )
        )
        await self._apply_reactive_steps(self.reactive_policy.processing_started(self.state))

        context = await self._build_context()
        await self._apply_turn_attention(context)
        available_components = self._available_components()
        available_capabilities = self.capability_registry.list_available(available_components)

        try:
            self._update_ai_debug(
                planning_active=False,
                response_active=False,
                plan_preview="routing...",
                response_preview=None,
            )
            logger.info(
                "turn_trace routing_started route_director=%s transcript_len=%s",
                type(self.turn_director).__name__,
                len(transcript.text),
            )
            plan = await self.turn_director.direct_turn(transcript, context, available_capabilities)
            await self.handle_event(
                Event(
                    name=EventName.PLAN_CREATED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"plan": plan},
                )
            )
            validated_plan, skipped_results = self.capability_registry.validate_plan(
                plan,
                available_components=available_components,
            )
            if not validated_plan.steps:
                if any(capability.capability_id == "cloud_reply" for capability in available_capabilities):
                    validated_plan = TurnPlan(
                        route_kind=RouteKind.CLOUD_CHAT,
                        confidence=0.0,
                        rationale="validator fell back to a single cloud reply step",
                        source="validator",
                        steps=(PlanStep(capability_id="cloud_reply", reason="fallback response generation"),),
                    )
                else:
                    raise RuntimeError("validated turn plan contained no executable steps")

            self.state.last_plan = validated_plan
            self._log_plan_selection(validated_plan)
            self._update_ai_debug(
                planning_active=False,
                plan_preview=self._ai_plan_preview(validated_plan),
            )
            if self.terminal_debug is not None:
                self.terminal_debug.update_runtime(
                    lifecycle=self.state.lifecycle.value,
                    emotion=self.state.emotion.value,
                    language=self.state.active_language.value,
                    route_summary=self._route_summary(validated_plan.route_kind),
                    last_error=self.state.last_error,
                )
            await self.handle_event(
                Event(
                    name=EventName.ROUTE_SELECTED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"route_kind": validated_plan.route_kind.value},
                )
            )
            await self.handle_event(
                Event(
                    name=EventName.PLAN_VALIDATED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"plan": validated_plan, "skipped_steps": skipped_results},
                )
            )
            response, step_results = await self.execute_plan(validated_plan, context, transcript, skipped_results)
        except Exception as exc:
            logger.exception("turn routing/execution failed")
            self.state.last_error = str(exc)
            self.state.last_plan = None
            step_results = ()
            self._update_ai_debug(
                planning_active=False,
                response_active=False,
                plan_preview="routing failed",
                response_preview="error",
            )
            await self.handle_event(
                Event(
                    name=EventName.ERROR_OCCURRED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"error": str(exc)},
                )
            )
            response = AiResponse(
                text="I hit a problem, but I am still here and ready for the next turn.",
                language=transcript.language,
                emotion=EmotionState.CURIOUS,
                intent="error_recovery",
            )

        self.state.last_step_results = step_results
        self.state.current_response = response.text
        self._update_ai_debug(
            planning_active=False,
            response_active=False,
            plan_preview=self._ai_plan_preview(self.state.last_plan),
            response_preview=response.text,
        )
        if self.terminal_debug is not None:
            route_summary = self._route_summary(self.state.last_plan.route_kind) if self.state.last_plan else "error"
            self.terminal_debug.update_runtime(
                lifecycle=self.state.lifecycle.value,
                emotion=self.state.emotion.value,
                language=self.state.active_language.value,
                route_summary=route_summary,
                last_error=self.state.last_error,
            )
        await self.handle_event(
            Event(
                name=EventName.RESPONSE_READY,
                source=ComponentName.ORCHESTRATOR,
                payload={"response": response},
            )
        )
        return await self._deliver_response(
            response,
            transcript,
            self.state.last_plan.route_kind if self.state.last_plan else RouteKind.CLOUD_CHAT,
            plan=self.state.last_plan,
            step_results=step_results,
        )

    async def execute_plan(
        self,
        plan: TurnPlan,
        context: InteractionContext,
        transcript: Transcript,
        skipped_results: tuple[PlanStepResult, ...] = (),
    ) -> tuple[AiResponse, tuple[PlanStepResult, ...]]:
        """Execute the selected multi-step plan and return the final reply."""

        completed_results: list[PlanStepResult] = []
        response: AiResponse | None = None

        for skipped in skipped_results:
            completed_results.append(skipped)
            await self.handle_event(
                Event(
                    name=EventName.STEP_SKIPPED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"result": skipped},
                )
            )

        for phase in (
            StepPhase.IMMEDIATE,
            StepPhase.QUERY,
            StepPhase.REPLY,
            StepPhase.CLEANUP,
        ):
            for step in plan.steps:
                if (step.phase or StepPhase.IMMEDIATE) is not phase:
                    continue
                step_result, cloud_response = await self._execute_plan_step(
                    step,
                    plan=plan,
                    context=context,
                    transcript=transcript,
                    prior_results=tuple(completed_results),
                )
                completed_results.append(step_result)
                if cloud_response is not None:
                    response = cloud_response

        if response is None:
            response = self._derive_response_from_results(completed_results)

        return response, tuple(completed_results)

    async def handle_event(self, event: Event) -> None:
        """Accept an event for routing history, subscribers, and debugging."""

        self.event_history.append(event)
        self.state.last_event_name = event.name.value
        logger.info("event=%s source=%s", event.name.value, event.source.value)
        if event.name is EventName.TTS_SYNTHESIS_STARTED:
            self._mark_turn_latency("tts_synth_start")
            self._log_turn_latency_span("cloud_done->tts_synth_start", "cloud_done", "tts_synth_start")
            logger.info(
                "turn_trace tts_synthesis_started job_id=%s text_len=%s",
                event.payload.get("job_id", "--"),
                len(str(event.payload.get("text", ""))),
            )
        elif event.name is EventName.TTS_SYNTHESIS_FINISHED:
            logger.info(
                "turn_trace tts_synthesis_finished job_id=%s text_len=%s",
                event.payload.get("job_id", "--"),
                len(str(event.payload.get("text", ""))),
            )
        elif event.name is EventName.TTS_PLAYBACK_STARTED:
            self._mark_turn_latency("tts_playback_started")
            self._log_turn_latency_span(
                "tts_synth_start->tts_playback_started",
                "tts_synth_start",
                "tts_playback_started",
            )
            self._log_turn_latency_span("total_to_first_audio", "wake_detected", "tts_playback_started")
            logger.info(
                "%s playback_started job_id=%s text_len=%s",
                "realtime_trace" if self.config.runtime.interaction_backend == "openai_realtime" else "turn_trace tts",
                event.payload.get("job_id", "--"),
                len(str(event.payload.get("text", ""))),
            )
        elif event.name is EventName.TTS_PLAYBACK_FINISHED:
            logger.info(
                "%s playback_finished job_id=%s duration_ms=%s",
                "realtime_trace" if self.config.runtime.interaction_backend == "openai_realtime" else "turn_trace tts",
                event.payload.get("job_id", "--"),
                event.payload.get("duration_ms", "--"),
            )
        elif event.name is EventName.TTS_INTERRUPTED:
            logger.info(
                "%s interrupted job_id=%s source=%s in=%sB/%sch out=%sB/%sch interrupts=%s",
                "realtime_trace" if self.config.runtime.interaction_backend == "openai_realtime" else "turn_trace tts",
                event.payload.get("job_id", "--"),
                event.payload.get("source", "--"),
                event.payload.get("input_audio_bytes", "--"),
                event.payload.get("input_audio_chunks", "--"),
                event.payload.get("output_audio_bytes", "--"),
                event.payload.get("output_audio_chunks", "--"),
                event.payload.get("interrupt_count", "--"),
            )
        elif event.name is EventName.RESPONSE_READY:
            logger.info(
                "turn_trace response_ready text_len=%s",
                len(str(getattr(event.payload.get("response"), "text", ""))),
            )
        self._update_realtime_debug_from_event(event)
        await self._apply_tts_lifecycle_event(event)
        await self.event_bus.publish(event)

    async def _build_context(self, *, include_history: bool = False) -> InteractionContext:
        active_user, detections = await asyncio.gather(
            self.memory.get_active_user(),
            self._safe_get_detections(),
        )
        history = await self.memory.get_recent_history() if include_history else ()
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

    async def _execute_plan_step(
        self,
        step: PlanStep,
        *,
        plan: TurnPlan,
        context: InteractionContext,
        transcript: Transcript,
        prior_results: tuple[PlanStepResult, ...],
    ) -> tuple[PlanStepResult, AiResponse | None]:
        await self.handle_event(
            Event(
                name=EventName.STEP_STARTED,
                source=ComponentName.ORCHESTRATOR,
                payload={"step": step},
            )
        )

        try:
            if step.capability_id == "look_at_user":
                action_result = await self.hardware.execute_action(ActionRequest(name="look_at_user"))
                await self.handle_event(
                    Event(
                        name=EventName.ACTION_EXECUTED,
                        source=ComponentName.HARDWARE,
                        payload={"result": action_result},
                    )
                )
                self._apply_state_changes(action_result.state_changes)
                result = PlanStepResult(
                    capability_id=step.capability_id,
                    success=action_result.success,
                    message=action_result.message,
                    state_changes=action_result.state_changes,
                )
                await self.handle_event(
                    Event(
                        name=EventName.STEP_FINISHED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": result},
                    )
                )
                return result, None

            if step.capability_id == "turn_head":
                action_result = await self.hardware.execute_action(
                    ActionRequest(name="turn_head", arguments=step.arguments)
                )
                await self.handle_event(
                    Event(
                        name=EventName.ACTION_EXECUTED,
                        source=ComponentName.HARDWARE,
                        payload={"result": action_result},
                    )
                )
                self._apply_state_changes(action_result.state_changes)
                result = PlanStepResult(
                    capability_id=step.capability_id,
                    success=action_result.success,
                    message=action_result.message,
                    state_changes=action_result.state_changes,
                )
                await self.handle_event(
                    Event(
                        name=EventName.STEP_FINISHED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": result},
                    )
                )
                return result, None

            if step.capability_id == "set_emotion":
                emotion = EmotionState(str(step.arguments.get("emotion", EmotionState.NEUTRAL.value)))
                preview = self.state.current_response or (
                    None if self.config.runtime.interactive_console else (self.state.current_transcript.text if self.state.current_transcript else None)
                )
                await self._set_lifecycle(self.state.lifecycle, emotion, preview)
                result = PlanStepResult(
                    capability_id=step.capability_id,
                    success=True,
                    message=f"Showing {emotion.value} emotion.",
                    state_changes={"emotion": emotion.value},
                )
                await self.handle_event(
                    Event(
                        name=EventName.STEP_FINISHED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": result},
                    )
                )
                return result, None

            if step.capability_id == "visible_people":
                query = await self._run_query("visible_people", context)
                result = PlanStepResult(
                    capability_id=step.capability_id,
                    success=True,
                    message=query.answer_text,
                    data=query.data,
                )
                await self.handle_event(
                    Event(
                        name=EventName.QUERY_EXECUTED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": query},
                    )
                )
                await self.handle_event(
                    Event(
                        name=EventName.STEP_FINISHED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": result},
                    )
                )
                return result, None

            if step.capability_id == "user_summary":
                query = await self._run_query("user_summary", context)
                result = PlanStepResult(
                    capability_id=step.capability_id,
                    success=True,
                    message=query.answer_text,
                    data=query.data,
                )
                await self.handle_event(
                    Event(
                        name=EventName.QUERY_EXECUTED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": query},
                    )
                )
                await self.handle_event(
                    Event(
                        name=EventName.STEP_FINISHED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": result},
                    )
                )
                return result, None

            if step.capability_id == "robot_status":
                query = await self._run_query("robot_status", context)
                result = PlanStepResult(
                    capability_id=step.capability_id,
                    success=True,
                    message=query.answer_text,
                    data=query.data,
                )
                await self.handle_event(
                    Event(
                        name=EventName.QUERY_EXECUTED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": query},
                    )
                )
                await self.handle_event(
                    Event(
                        name=EventName.STEP_FINISHED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": result},
                    )
                )
                return result, None

            if step.capability_id == "cloud_reply":
                self._update_ai_debug(
                    response_active=True,
                    response_preview="responding...",
                )
                reply = await self._run_cloud_reply(transcript, context, plan, prior_results)
                self._update_ai_debug(
                    response_active=False,
                    response_preview=reply.text,
                )
                result = PlanStepResult(
                    capability_id=step.capability_id,
                    success=True,
                    message=reply.text,
                    data={"emotion": reply.emotion.value},
                )
                await self.handle_event(
                    Event(
                        name=EventName.STEP_FINISHED,
                        source=ComponentName.ORCHESTRATOR,
                        payload={"result": result},
                    )
                )
                return result, reply

            raise RuntimeError(f"unsupported capability '{step.capability_id}'")
        except Exception as exc:
            logger.exception("plan step failed")
            self.state.last_error = str(exc)
            await self.handle_event(
                Event(
                    name=EventName.ERROR_OCCURRED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"error": str(exc), "step": step.capability_id},
                )
            )
            result = PlanStepResult(
                capability_id=step.capability_id,
                success=False,
                message=f"Failed to execute {step.capability_id}: {exc}",
            )
            await self.handle_event(
                Event(
                    name=EventName.STEP_FINISHED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"result": result},
                )
            )
            return result, None

    async def _run_query(
        self,
        query_name: str,
        context: InteractionContext,
    ) -> QueryResult:
        if query_name == "visible_people":
            detections = context.current_detections
            if not detections:
                return QueryResult(answer_text="I do not see anyone right now.")

            names = ", ".join(detection.label for detection in detections)
            return QueryResult(answer_text=f"I can currently see {names}.", data={"detections": names})

        if query_name == "user_summary":
            if context.active_user is None:
                return QueryResult(answer_text="I do not know much about you yet.")
            summary = context.active_user.summary or (
                f"I know you as {context.active_user.display_name or context.active_user.user_id}."
            )
            return QueryResult(answer_text=summary)

        if query_name == "robot_status":
            return QueryResult(
                answer_text=(
                    f"I am {self.state.lifecycle.value}, my eyes are "
                    f"{'open' if self.state.eyes_open else 'closed'}, "
                    f"and my head is pointing {self.state.head_direction}."
                )
            )

        return QueryResult(answer_text="I do not have an answer for that local query yet.")

    async def _run_cloud_reply(
        self,
        transcript: Transcript,
        context: InteractionContext,
        plan: TurnPlan,
        prior_results: tuple[PlanStepResult, ...],
    ) -> AiResponse:
        previous_response_id = self._resumable_openai_response_id()
        try:
            logger.info(
                "turn_trace cloud_request_sent model=%s previous_response_id=%s transcript_len=%s plan_steps=%s",
                getattr(self.cloud_response, "model", "cloud"),
                previous_response_id or "--",
                len(transcript.text),
                len(plan.steps),
            )
            reply_result = await self.cloud_response.generate_reply(
                transcript,
                context,
                plan,
                prior_results,
                previous_response_id=previous_response_id,
                tool_handler=self._handle_cloud_tool_request,
            )
            if reply_result.first_byte_at is not None:
                self._mark_turn_latency("cloud_first_byte", reply_result.first_byte_at)
            if reply_result.finished_at is not None:
                self._mark_turn_latency("cloud_done", reply_result.finished_at)
            else:
                self._mark_turn_latency("cloud_done")
            self._log_turn_latency_span("stt_final_ready->cloud_first_byte", "stt_final_ready", "cloud_first_byte")
            self._log_turn_latency_span("cloud_first_byte->cloud_done", "cloud_first_byte", "cloud_done")
            self._record_openai_reply(reply_result.response_id)
            logger.info(
                "turn_trace cloud_response_received response_id=%s text_len=%s",
                reply_result.response_id or "--",
                len(reply_result.response.text),
            )
            return reply_result.response
        except Exception as exc:
            logger.exception("cloud response failed")
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
                language=transcript.language,
                emotion=EmotionState.CURIOUS,
                intent="cloud_fallback",
            )

    def _resumable_openai_response_id(self) -> str | None:
        response_id = self.state.last_openai_response_id
        response_at = self.state.last_openai_response_at
        if response_id is None or response_at is None:
            return None
        age_seconds = (datetime.now(UTC) - response_at).total_seconds()
        if age_seconds > _OPENAI_RESPONSE_RESUME_WINDOW_SECONDS:
            self.state.last_openai_response_id = None
            self.state.last_openai_response_at = None
            return None
        return response_id

    def _record_openai_reply(self, response_id: str | None) -> None:
        if not response_id:
            return
        self.state.last_openai_response_id = response_id
        self.state.last_openai_response_at = datetime.now(UTC)

    async def _handle_cloud_tool_request(self, request: CloudToolRequest) -> CloudToolResult:
        if request.tool_name == "camera_snapshot":
            try:
                await self.ui.show_text("Let me take a look.")
                await self._speak_text(
                    "Let me take a look.",
                    preview_text="Let me take a look.",
                    record_events=False,
                )
                snapshot = await self.vision.capture_snapshot()
                summary = snapshot.summary or "I captured the current camera view."
                await self._set_lifecycle(
                    LifecycleStage.PROCESSING,
                    EmotionState.CURIOUS,
                    self.state.current_transcript.text if self.state.current_transcript else None,
                )
                return CloudToolResult(
                    call_id=request.call_id,
                    tool_name=request.tool_name,
                    output_text=summary,
                    image_url=snapshot.image_url,
                )
            except Exception as exc:
                logger.exception("tool request failed")
                self.state.last_error = str(exc)
                await self.handle_event(
                    Event(
                        name=EventName.ERROR_OCCURRED,
                        source=ComponentName.VISION,
                        payload={"error": str(exc), "tool": request.tool_name},
                    )
                )
                await self._set_lifecycle(
                    LifecycleStage.PROCESSING,
                    EmotionState.CURIOUS,
                    self.state.current_transcript.text if self.state.current_transcript else None,
                )
                return CloudToolResult(
                    call_id=request.call_id,
                    tool_name=request.tool_name,
                    output_text="I could not capture a photo right now.",
                )

        return CloudToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            output_text=f"The tool '{request.tool_name}' is not available on this robot.",
        )

    async def handle_realtime_tool_request(self, request: RealtimeToolCall) -> RealtimeToolResult:
        """Validate and execute a realtime model tool request on the Pi."""

        plan = TurnPlan(
            route_kind=RouteKind.HYBRID,
            confidence=1.0,
            rationale="validated realtime tool request",
            source="openai_realtime",
            steps=(
                PlanStep(
                    capability_id=request.tool_name,
                    arguments=request.arguments,
                    phase=StepPhase.QUERY,
                    reason="model requested local robot tool",
                ),
            ),
        )
        validated_plan, skipped = self.capability_registry.validate_plan(
            plan,
            available_components=self._available_components(),
        )
        if skipped or not validated_plan.steps:
            message = skipped[0].message if skipped else f"Tool '{request.tool_name}' is not available."
            return RealtimeToolResult(
                call_id=request.call_id,
                tool_name=request.tool_name,
                output_text=message,
            )

        if request.tool_name == "camera_snapshot":
            try:
                snapshot = await self.vision.capture_snapshot()
                return RealtimeToolResult(
                    call_id=request.call_id,
                    tool_name=request.tool_name,
                    output_text=snapshot.summary or "I captured the current camera view.",
                    image_url=snapshot.image_url,
                )
            except Exception as exc:
                logger.exception("realtime camera tool failed")
                return RealtimeToolResult(
                    call_id=request.call_id,
                    tool_name=request.tool_name,
                    output_text=f"Camera snapshot failed: {exc}",
                )

        context = await self._build_context()
        transcript = self.state.current_transcript or Transcript(
            text="",
            language=self.state.active_language,
            confidence=0.0,
            is_final=False,
        )
        step = validated_plan.steps[0]
        result, _response = await self._execute_plan_step(
            step,
            plan=validated_plan,
            context=context,
            transcript=transcript,
            prior_results=(),
        )
        return RealtimeToolResult(
            call_id=request.call_id,
            tool_name=request.tool_name,
            output_text=result.message,
        )

    async def _apply_turn_attention(self, context: InteractionContext) -> None:
        has_attention_target = bool(context.current_detections or context.active_user)
        if not has_attention_target or self.state.head_direction == "user":
            return
        await self._apply_reactive_steps(
            (
                PlanStep(
                    capability_id="look_at_user",
                    phase=StepPhase.REACTIVE,
                    reason="maintain visual attention before replying",
                ),
            )
        )

    async def _speak_text(
        self,
        text: str,
        *,
        preview_text: str | None = None,
        record_events: bool = True,
        language: Language | None = None,
    ):
        del record_events
        del preview_text
        return await self.tts.speak(
            SpeechRequest(
                text=text,
                language=language or self.state.active_language,
            )
        )

    def _derive_response_from_results(self, results: list[PlanStepResult]) -> AiResponse:
        for result in reversed(results):
            if not result.success or result.skipped:
                continue
            if result.capability_id in {"look_at_user", "turn_head"}:
                return AiResponse(
                    text=result.message,
                    language=self.state.active_language,
                    emotion=EmotionState.HAPPY,
                    intent=result.capability_id,
                )
            if result.capability_id == "set_emotion":
                return AiResponse(
                    text=result.message,
                    language=self.state.active_language,
                    emotion=self.state.emotion,
                    intent=result.capability_id,
                    should_speak=False,
                )
            return AiResponse(
                text=result.message,
                language=self.state.active_language,
                emotion=EmotionState.CURIOUS,
                intent=result.capability_id,
            )

        return AiResponse(
            text="I am ready for the next turn.",
            language=self.state.active_language,
            emotion=EmotionState.NEUTRAL,
            intent="noop",
            should_speak=False,
        )

    async def _deliver_response(
        self,
        response: AiResponse,
        transcript: Transcript,
        route_kind: RouteKind,
        *,
        plan: TurnPlan | None,
        step_results: tuple[PlanStepResult, ...],
    ) -> bool:
        display_text = response.display_text or response.text
        response_language = response.language or transcript.language
        self.state.active_language = response_language
        self.state.response_emotion = response.emotion
        spoke_reply_successfully = False
        await self._set_lifecycle(LifecycleStage.RESPONDING, response.emotion, display_text)
        await self.ui.show_text(display_text)

        if response.should_speak:
            try:
                speech_output = await self._speak_text(
                    response.text,
                    preview_text=display_text,
                    language=response_language,
                )
                spoke_reply_successfully = speech_output.status is SpeechJobStatus.PLAYBACK_FINISHED
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
                language=response_language,
                timestamp=datetime.now(UTC),
                route_kind=route_kind,
                user_id=self.state.active_user_id,
                plan_summary=self._plan_summary(plan),
                executed_steps=tuple(
                    result.capability_id
                    for result in step_results
                    if result.success and not result.skipped
                ),
            )
        )
        self.state.response_emotion = None
        await self._set_lifecycle(LifecycleStage.IDLE, EmotionState.NEUTRAL)
        return response.should_speak and spoke_reply_successfully

    async def _apply_tts_lifecycle_event(self, event: Event) -> None:
        if event.source is not ComponentName.TTS:
            return

        preview_text = self.state.current_response
        response_emotion = self.state.response_emotion or EmotionState.NEUTRAL

        if event.name is EventName.TTS_PLAYBACK_STARTED:
            await self._set_lifecycle(
                LifecycleStage.SPEAKING,
                EmotionState.SPEAKING,
                preview_text,
            )
            return

        if (
            self.config.runtime.interaction_backend == "openai_realtime"
            and event.name is EventName.TTS_INTERRUPTED
        ):
            await self._set_lifecycle(
                LifecycleStage.LISTENING,
                EmotionState.LISTENING,
                preview_text,
            )
            return

        if event.name in {EventName.TTS_PLAYBACK_FINISHED, EventName.TTS_INTERRUPTED, EventName.TTS_FAILED}:
            if self.state.lifecycle is not LifecycleStage.SPEAKING:
                return
            await self._set_lifecycle(
                LifecycleStage.RESPONDING,
                response_emotion,
                preview_text,
            )

    def _update_realtime_debug_from_event(self, event: Event) -> None:
        if self.config.runtime.interaction_backend != "openai_realtime":
            return
        if event.source is not ComponentName.TTS:
            return
        if self.terminal_debug is None:
            return
        update_realtime_status = getattr(self.terminal_debug, "update_realtime_status", None)
        if not callable(update_realtime_status):
            return
        phase = None
        if event.name is EventName.TTS_PLAYBACK_STARTED:
            phase = "speaking"
        elif event.name is EventName.TTS_PLAYBACK_FINISHED:
            phase = "listening"
        elif event.name is EventName.TTS_INTERRUPTED:
            phase = "listening"
        elif event.name is EventName.TTS_FAILED:
            phase = "error"
        update_realtime_status(
            phase=phase,
            voice=str(event.payload.get("voice_id", "")) or None,
            input_audio_bytes=_payload_int(event.payload.get("input_audio_bytes")),
            input_audio_chunks=_payload_int(event.payload.get("input_audio_chunks")),
            output_audio_bytes=_payload_int(event.payload.get("output_audio_bytes")),
            output_audio_chunks=_payload_int(event.payload.get("output_audio_chunks")),
            response_count=_payload_int(event.payload.get("response_count")),
            interrupt_count=_payload_int(event.payload.get("interrupt_count")),
            last_event=event.name.value,
        )

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

    def _available_components(self) -> set[ComponentName]:
        available = {
            ComponentName.ORCHESTRATOR,
            ComponentName.UI,
            ComponentName.MEMORY,
            ComponentName.HARDWARE,
            ComponentName.VISION,
            ComponentName.TTS,
        }
        if self.cloud_response is not None:
            available.add(ComponentName.CLOUD)
        return available

    async def _apply_reactive_steps(self, steps: tuple[PlanStep, ...]) -> tuple[PlanStepResult, ...]:
        if not steps:
            return ()

        plan = TurnPlan(
            route_kind=RouteKind.HYBRID,
            confidence=1.0,
            rationale="reactive local behavior",
            source="reactive_policy",
            steps=steps,
        )
        validated_plan, skipped = self.capability_registry.validate_plan(
            plan,
            available_components=self._available_components(),
        )
        results: list[PlanStepResult] = list(skipped)

        for skipped_result in skipped:
            await self.handle_event(
                Event(
                    name=EventName.STEP_SKIPPED,
                    source=ComponentName.ORCHESTRATOR,
                    payload={"result": skipped_result},
                )
            )

        if self.state.current_transcript is None:
            transcript = Transcript(
                text="",
                language=self.state.active_language,
                confidence=0.0,
                is_final=False,
            )
        else:
            transcript = self.state.current_transcript

        context = InteractionContext(
            active_user=None,
            recent_history=(),
            current_detections=self.state.last_detections,
            robot_state=RobotStateSnapshot(
                lifecycle=self.state.lifecycle.value,
                emotion=self.state.emotion,
                eyes_open=self.state.eyes_open,
                head_direction=self.state.head_direction,
            ),
        )

        for step in validated_plan.steps:
            result, _response = await self._execute_plan_step(
                step,
                plan=validated_plan,
                context=context,
                transcript=transcript,
                prior_results=tuple(results),
            )
            results.append(result)

        return tuple(results)

    def _plan_summary(self, plan: TurnPlan | None) -> str | None:
        if plan is None:
            return None
        step_ids = ", ".join(step.capability_id for step in plan.steps)
        if not step_ids:
            step_ids = "no-steps"
        if plan.rationale:
            return f"{plan.route_kind.value}: {step_ids} ({plan.rationale})"
        return f"{plan.route_kind.value}: {step_ids}"

    async def _set_lifecycle(
        self,
        lifecycle: LifecycleStage,
        emotion: EmotionState,
        preview_text: str | None = None,
    ) -> None:
        self.state.lifecycle = lifecycle
        self.state.emotion = emotion
        await self.ui.render_state(lifecycle.value, emotion.value, preview_text)
        if self.terminal_debug is not None:
            self.terminal_debug.update_runtime(
                lifecycle=lifecycle.value,
                emotion=emotion.value,
                language=self.state.active_language.value,
                route_summary=self._route_summary(self.state.last_plan.route_kind) if self.state.last_plan else None,
                last_error=self.state.last_error,
            )
            if lifecycle is LifecycleStage.IDLE:
                self._update_ai_debug(
                    planning_active=False,
                    response_active=False,
                )

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

    def _show_transcript_update(self, transcript: Transcript, *, is_final: bool) -> None:
        if not self.config.runtime.interactive_console:
            return

        if self.terminal_debug is not None:
            self._update_terminal_debug_transcript(transcript, is_final=is_final)
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

    def _update_terminal_debug_transcript(self, transcript: Transcript, *, is_final: bool) -> None:
        if self.terminal_debug is None:
            return
        self.terminal_debug.update_transcript(
            transcript.text,
            language=transcript.language.value,
            is_final=is_final,
        )

    def _route_summary(self, route_kind: RouteKind) -> str:
        return route_kind.value.replace("_", " ")

    def _ai_plan_preview(self, plan: TurnPlan | None) -> str | None:
        if plan is None or not plan.steps:
            return None
        return " -> ".join(step.capability_id for step in plan.steps)

    def _update_ai_debug(
        self,
        *,
        planning_active: bool | None = None,
        response_active: bool | None = None,
        plan_preview: str | None = None,
        response_preview: str | None = None,
    ) -> None:
        if self.terminal_debug is None:
            return
        backend = "mock" if self.config.runtime.use_mock_ai else (self.config.cloud.provider_name or "cloud")
        self.terminal_debug.update_ai_status(
            backend=backend,
            planning_active=planning_active,
            response_active=response_active,
            plan_preview=plan_preview,
            response_preview=response_preview,
        )

    def _log_plan_selection(self, plan: TurnPlan) -> None:
        formatter = ConsoleFormatter()
        rationale = f" rationale={plan.rationale}" if plan.rationale else ""
        plain = formatter.stamp(
            f"[ROUTE] kind={plan.route_kind.value} confidence={plan.confidence:.2f}{rationale}"
        )
        formatter.emit(
            formatter.stamp(
                f"{formatter.route_label('[ROUTE]')} "
                f"{formatter.response(plan.route_kind.value)} "
                f"confidence={plan.confidence:.2f}"
                f"{formatter.label(' rationale=') + plan.rationale if plan.rationale else ''}"
            ),
            plain_text=plain,
        )

    def _strip_wake_phrase_from_transcript(self, transcript: Transcript) -> Transcript:
        if not self.config.runtime.wake_word_enabled or not self.config.runtime.wake_word_phrase.strip():
            return transcript
        stripped_text = strip_wake_phrase(transcript.text, self.config.runtime.wake_word_phrase)
        if stripped_text is None:
            return transcript
        return Transcript(
            text=stripped_text,
            language=transcript.language,
            confidence=transcript.confidence,
            is_final=transcript.is_final,
            started_at=transcript.started_at,
            ended_at=transcript.ended_at,
            metadata=dict(transcript.metadata),
        )

    def _show_interactive_speech_hint(self) -> None:
        formatter = ConsoleFormatter()
        if self.terminal_debug is not None and self.wake_word is not None:
            self.terminal_debug.update_wake_status(
                "listening",
                self.config.runtime.wake_word_phrase.strip() or "--",
            )
        plain = (
            "[CTRL] Type a phrase and press Enter, press Enter on an empty line to listen now, "
            "say the wake word, or type exit to quit."
        )
        formatter.emit(
            formatter.stamp(f"{formatter.label('[CTRL]')} {plain.removeprefix('[CTRL] ')}"),
            plain_text=formatter.stamp(plain),
        )

    def _clear_wake_handoff(self) -> None:
        if self.stt is not None and hasattr(self.stt, "begin_utterance"):
            return
        if self.stt is not None and hasattr(self.stt, "prime_wake_audio"):
            self.stt.prime_wake_audio(None)

    def _mark_manual_listening_awake(self) -> None:
        if self.terminal_debug is None or self.wake_word is None:
            return
        self.terminal_debug.update_wake_status(
            "awake",
            self.config.runtime.wake_word_phrase.strip() or "--",
        )

    def _begin_manual_utterance(self) -> None:
        self._start_turn_latency_window()
        self._active_speech_trigger = "manual"
        if self.stt is not None and hasattr(self.stt, "begin_utterance"):
            self.stt.begin_utterance(trigger="manual")

    def _begin_follow_up_utterance(self) -> None:
        self._start_turn_latency_window()
        self._active_speech_trigger = "follow_up"
        if self.stt is not None and hasattr(self.stt, "begin_utterance"):
            self.stt.begin_utterance(trigger="follow_up")

    def _start_turn_latency_window(self) -> None:
        self._turn_latency_marks.clear()
        self._mark_turn_latency("wake_detected")

    def _mark_turn_latency(self, name: str, when: datetime | None = None) -> None:
        self._turn_latency_marks[name] = when or datetime.now(UTC)

    def _log_turn_latency_span(self, label: str, start_key: str, end_key: str) -> None:
        started_at = self._turn_latency_marks.get(start_key)
        ended_at = self._turn_latency_marks.get(end_key)
        if started_at is None or ended_at is None:
            return
        duration_ms = max(0.0, (ended_at - started_at).total_seconds() * 1000.0)
        logger.info(
            "turn_latency span=%s duration_ms=%.1f start=%s end=%s",
            label,
            duration_ms,
            start_key,
            end_key,
        )

    def _should_continue_follow_up_session(self, outcome: SpeechTurnOutcome) -> bool:
        if outcome.transcript_empty:
            return False
        if not self.config.runtime.follow_up_mode_enabled:
            return False
        return outcome.follow_up_eligible

    def _show_typed_input_preview(self, text: str) -> None:
        if self.terminal_debug is None:
            return
        preview = "Type a phrase, press Enter to listen now, or say the wake word." if not text else f"input> {text}"
        self.terminal_debug.update_transcript(preview, language=self.state.active_language.value, is_final=False)

    def _clear_typed_input_preview(self) -> None:
        if self.terminal_debug is None:
            return
        self.terminal_debug.update_transcript("", language=self.state.active_language.value, is_final=False)


def _read_console_line_ready(timeout_seconds: float) -> str | None:
    """Return a completed stdin line when available without blocking indefinitely."""

    isatty = getattr(sys.stdin, "isatty", None)
    if not isatty or not isatty():
        return builtins.input("")

    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if not ready:
        return None
    line = sys.stdin.readline()
    if line == "":
        raise EOFError()
    return line.rstrip("\n")


def _read_console_char_ready(timeout_seconds: float) -> str | None:
    """Return one stdin character when available without blocking indefinitely."""

    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if not ready:
        return None
    data = os.read(sys.stdin.fileno(), 1)
    if not data:
        raise EOFError()
    return data.decode(errors="ignore")


@contextlib.contextmanager
def _stdin_cbreak_mode():
    """Temporarily disable canonical input and echo for interactive key handling."""

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    modified = termios.tcgetattr(fd)
    modified[3] &= ~(termios.ICANON | termios.ECHO)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, modified)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)

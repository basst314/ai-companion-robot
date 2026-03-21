"""Smoke tests for the initial package scaffold."""

from main import main
from orchestrator.service import OrchestratorService
from orchestrator.state import LifecycleStage, OrchestratorState
from shared.config import AppConfig
from shared.events import Event, EventName
from shared.models import ComponentName, Language


def test_main_returns_success_code() -> None:
    """The placeholder entry point should construct basic application objects."""

    assert main() == 0


def test_orchestrator_state_defaults_are_available() -> None:
    """The scaffold should expose a predictable initial state."""

    state = OrchestratorState.initial()
    assert state.lifecycle is LifecycleStage.IDLE
    assert state.active_language is Language.ENGLISH


def test_core_types_can_be_instantiated() -> None:
    """Shared types should be importable and constructible."""

    service = OrchestratorService(config=AppConfig(), state=OrchestratorState.initial())
    event = Event(name=EventName.SPEECH_DETECTED, source=ComponentName.STT)
    assert service.state.last_event is None
    assert event.payload == {}


"""Typed application configuration placeholders."""

from dataclasses import dataclass, field
from pathlib import Path

from shared.models import Language


@dataclass(slots=True)
class PathConfig:
    """Filesystem locations used by the local runtime."""

    data_dir: Path = Path("data")
    models_dir: Path = Path("models")
    logs_dir: Path = Path("logs")


@dataclass(slots=True)
class CloudConfig:
    """Minimal cloud configuration for future provider wiring."""

    enabled: bool = False
    provider_name: str | None = "mock-cloud"


@dataclass(slots=True)
class RuntimeConfig:
    """Runtime feature flags and manual input configuration."""

    auto_run: bool = False
    interactive_console: bool = False
    manual_inputs: tuple[str, ...] = ()
    use_mock_stt: bool = True
    use_mock_tts: bool = True
    use_mock_ai: bool = True
    use_mock_vision: bool = True
    use_mock_hardware: bool = True


@dataclass(slots=True)
class MockDataConfig:
    """Deterministic mock data used during development and testing."""

    visible_people: tuple[str, ...] = ("Sebastian",)
    active_user_id: str = "sebastian"
    active_user_name: str = "Sebastian"
    active_user_summary: str = "You are Sebastian, the robot's builder."


@dataclass(slots=True)
class AppConfig:
    """Top-level application configuration placeholder."""

    default_language: Language = Language.ENGLISH
    paths: PathConfig = field(default_factory=PathConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    mocks: MockDataConfig = field(default_factory=MockDataConfig)

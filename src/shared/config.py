"""Typed application configuration placeholders."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
from typing import Literal

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
    input_mode: Literal["manual", "speech"] = "manual"
    interactive_console: bool = False
    manual_inputs: tuple[str, ...] = ()
    stt_backend: Literal["mock", "whisper_cpp"] = "mock"
    whisper_model_path: Path | None = None
    whisper_binary_path: Path | None = None
    audio_record_command: tuple[str, ...] = ()
    record_seconds: int = 5
    language_mode: Literal["auto", "en", "de", "id"] = "auto"
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


ENV_FILE_NAMES = (".env", ".env.local")
ENV_PREFIX = "AI_COMPANION_"


def load_app_config(base_dir: Path | None = None) -> AppConfig:
    """Load application config from environment variables and local env files."""

    config = AppConfig()
    env = _load_environment(base_dir)

    config.default_language = _parse_language(
        env.get(f"{ENV_PREFIX}DEFAULT_LANGUAGE"),
        default=config.default_language,
    )
    config.paths.data_dir = _parse_path(
        env.get(f"{ENV_PREFIX}DATA_DIR"),
        default=config.paths.data_dir,
    )
    config.paths.models_dir = _parse_path(
        env.get(f"{ENV_PREFIX}MODELS_DIR"),
        default=config.paths.models_dir,
    )
    config.paths.logs_dir = _parse_path(
        env.get(f"{ENV_PREFIX}LOGS_DIR"),
        default=config.paths.logs_dir,
    )

    config.cloud.enabled = _parse_bool(
        env.get(f"{ENV_PREFIX}CLOUD_ENABLED"),
        default=config.cloud.enabled,
    )
    config.cloud.provider_name = env.get(f"{ENV_PREFIX}CLOUD_PROVIDER_NAME", config.cloud.provider_name)

    runtime = config.runtime
    runtime.auto_run = _parse_bool(env.get(f"{ENV_PREFIX}AUTO_RUN"), default=runtime.auto_run)
    runtime.input_mode = _parse_input_mode(
        env.get(f"{ENV_PREFIX}INPUT_MODE"),
        default=runtime.input_mode,
    )
    runtime.interactive_console = _parse_bool(
        env.get(f"{ENV_PREFIX}INTERACTIVE_CONSOLE"),
        default=runtime.interactive_console,
    )
    runtime.manual_inputs = _parse_csv_tuple(
        env.get(f"{ENV_PREFIX}MANUAL_INPUTS"),
        default=runtime.manual_inputs,
    )
    runtime.stt_backend = _parse_stt_backend(
        env.get(f"{ENV_PREFIX}STT_BACKEND"),
        default=runtime.stt_backend,
    )
    runtime.whisper_model_path = _parse_optional_path(env.get(f"{ENV_PREFIX}WHISPER_MODEL_PATH"))
    runtime.whisper_binary_path = _parse_optional_path(env.get(f"{ENV_PREFIX}WHISPER_BINARY_PATH"))
    runtime.audio_record_command = _parse_command(
        env.get(f"{ENV_PREFIX}AUDIO_RECORD_COMMAND"),
        default=runtime.audio_record_command,
    )
    runtime.record_seconds = _parse_int(
        env.get(f"{ENV_PREFIX}RECORD_SECONDS"),
        default=runtime.record_seconds,
    )
    runtime.language_mode = _parse_language_mode(
        env.get(f"{ENV_PREFIX}LANGUAGE_MODE"),
        default=runtime.language_mode,
    )
    runtime.use_mock_tts = _parse_bool(
        env.get(f"{ENV_PREFIX}USE_MOCK_TTS"),
        default=runtime.use_mock_tts,
    )
    runtime.use_mock_ai = _parse_bool(
        env.get(f"{ENV_PREFIX}USE_MOCK_AI"),
        default=runtime.use_mock_ai,
    )
    runtime.use_mock_vision = _parse_bool(
        env.get(f"{ENV_PREFIX}USE_MOCK_VISION"),
        default=runtime.use_mock_vision,
    )
    runtime.use_mock_hardware = _parse_bool(
        env.get(f"{ENV_PREFIX}USE_MOCK_HARDWARE"),
        default=runtime.use_mock_hardware,
    )

    return config


def _load_environment(base_dir: Path | None) -> dict[str, str]:
    """Merge process environment over repo-local env files."""

    repo_dir = base_dir or Path.cwd()
    merged: dict[str, str] = {}
    for filename in ENV_FILE_NAMES:
        env_path = repo_dir / filename
        if env_path.exists():
            merged.update(_parse_env_file(env_path))

    merged.update(os.environ)
    return merged


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE environment file."""

    parsed: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        parsed[key] = value
    return parsed


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    return int(value.strip())


def _parse_csv_tuple(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None or not value.strip():
        return default
    return tuple(item.strip() for item in value.split("||") if item.strip())


def _parse_command(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None or not value.strip():
        return default
    return tuple(shlex.split(value))


def _parse_path(value: str | None, default: Path) -> Path:
    if value is None or not value.strip():
        return default
    return Path(value)


def _parse_optional_path(value: str | None) -> Path | None:
    if value is None or not value.strip():
        return None
    return Path(value)


def _parse_language(value: str | None, default: Language) -> Language:
    if value == Language.GERMAN.value:
        return Language.GERMAN
    if value == Language.INDONESIAN.value:
        return Language.INDONESIAN
    if value == Language.ENGLISH.value:
        return Language.ENGLISH
    return default


def _parse_input_mode(value: str | None, default: Literal["manual", "speech"]) -> Literal["manual", "speech"]:
    if value in {"manual", "speech"}:
        return value
    return default


def _parse_stt_backend(
    value: str | None,
    default: Literal["mock", "whisper_cpp"],
) -> Literal["mock", "whisper_cpp"]:
    if value in {"mock", "whisper_cpp"}:
        return value
    return default


def _parse_language_mode(
    value: str | None,
    default: Literal["auto", "en", "de", "id"],
) -> Literal["auto", "en", "de", "id"]:
    if value in {"auto", "en", "de", "id"}:
        return value
    return default

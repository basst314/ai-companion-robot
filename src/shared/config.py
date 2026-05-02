"""Typed application configuration placeholders."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import sys
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
    """Cloud response configuration."""

    enabled: bool = False
    provider_name: str | None = "openai"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1/responses"
    openai_response_model: str = ""
    openai_timeout_seconds: float = 20.0
    openai_reply_max_output_tokens: int = 120
    openai_realtime_model: str = "gpt-realtime-1.5"
    openai_realtime_voice: str = "echo"
    openai_realtime_turn_detection: str = "semantic_vad"
    openai_realtime_turn_eagerness: str = "auto"
    openai_realtime_local_barge_in_enabled: bool = False
    openai_realtime_interrupt_response: bool = False
    openai_realtime_playback_barge_in_enabled: bool = True
    openai_realtime_playback_barge_in_threshold: float = 1800.0
    openai_realtime_playback_barge_in_required_ms: int = 160
    openai_realtime_playback_barge_in_grace_ms: int = 450
    openai_realtime_playback_barge_in_recent_vad_ms: int = 1800
    openai_realtime_playback_barge_in_recent_required_ms: int = 40
    openai_realtime_base_url: str = "wss://api.openai.com/v1/realtime"
    openai_realtime_audio_sample_rate: int = 24000


@dataclass(slots=True)
class RuntimeConfig:
    """Runtime feature flags and manual input configuration."""

    auto_run: bool = False
    interaction_backend: Literal["turn_based", "openai_realtime"] = "turn_based"
    input_mode: Literal["manual", "speech"] = "manual"
    interactive_console: bool = False
    manual_inputs: tuple[str, ...] = ()
    audio_init_command: tuple[str, ...] = ()
    audio_record_command: tuple[str, ...] = ()
    audio_input_channels: int = 1
    audio_channel_index: int = 0
    audio_output_backend: Literal["command", "alsa_persistent"] = "command"
    audio_play_command: tuple[str, ...] = ()
    audio_alsa_device: str = "default"
    audio_alsa_sample_rate: int = 24000
    audio_alsa_period_frames: int = 512
    audio_alsa_buffer_frames: int = 2048
    audio_alsa_keepalive_interval_ms: int = 20
    audio_save_session_recording: bool = False
    audio_session_recording_dir: Path = Path("data/audio/session-recordings")
    wake_word_enabled: bool = False
    follow_up_mode_enabled: bool = True
    follow_up_listen_timeout_seconds: float = 5.0
    follow_up_max_turns: int = 10
    wake_word_phrase: str = ""
    wake_word_model: str = ""
    wake_word_threshold: float = 0.5
    wake_lookback_seconds: float = 0.8
    language_mode: Literal["auto", "en", "de", "id"] = "auto"
    use_mock_ai: bool = True
    use_mock_vision: bool = True
    use_mock_hardware: bool = True


@dataclass(slots=True)
class UiConfig:
    """Browser-backed face display configuration."""

    backend: Literal["mock", "browser"] = "mock"
    idle_sleep_seconds: float = 300.0
    sleeping_eyes_grace_seconds: float = 12.0
    show_text_overlay: bool = True
    sleep_command: tuple[str, ...] = ()
    wake_command: tuple[str, ...] = ()
    browser_host: str = "127.0.0.1"
    browser_http_port: int = 8765
    browser_ws_port: int = 8766
    browser_launch_mode: Literal["kiosk", "windowed", "connect_only"] = "windowed"
    browser_executable: str = ""
    browser_profile_dir: Path | None = None
    browser_extra_args: tuple[str, ...] = ()
    browser_state_path: Path | None = None
    face_idle_enabled: bool = True
    face_idle_frequency: float = 0.26
    face_idle_intensity: float = 0.63
    face_idle_pause_randomness: float = 0.54
    face_secondary_micro_motion: bool = True
    face_idle_behaviors: tuple[str, ...] = (
        "blink",
        "look_side",
        "quick_glance",
        "bored",
        "curious",
        "scoot",
        "boundary_press",
    )


@dataclass(slots=True)
class MockDataConfig:
    """Deterministic mock data used during development and testing."""

    visible_people: tuple[str, ...] = ("Builder",)
    active_user_id: str = "builder"
    active_user_name: str = "Builder"
    active_user_summary: str = "You are the robot's builder."


@dataclass(slots=True)
class AppConfig:
    """Top-level application configuration placeholder."""

    default_language: Language = Language.ENGLISH
    paths: PathConfig = field(default_factory=PathConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    ui: UiConfig = field(default_factory=UiConfig)
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
    config.cloud.openai_api_key = env.get(f"{ENV_PREFIX}OPENAI_API_KEY", config.cloud.openai_api_key).strip()
    config.cloud.openai_base_url = env.get(f"{ENV_PREFIX}OPENAI_BASE_URL", config.cloud.openai_base_url).strip()
    config.cloud.openai_response_model = env.get(
        f"{ENV_PREFIX}OPENAI_RESPONSE_MODEL",
        config.cloud.openai_response_model,
    ).strip()
    config.cloud.openai_timeout_seconds = _parse_float(
        env.get(f"{ENV_PREFIX}OPENAI_TIMEOUT_SECONDS"),
        default=config.cloud.openai_timeout_seconds,
    )
    config.cloud.openai_reply_max_output_tokens = _parse_int(
        env.get(f"{ENV_PREFIX}OPENAI_REPLY_MAX_OUTPUT_TOKENS"),
        default=config.cloud.openai_reply_max_output_tokens,
    )
    config.cloud.openai_realtime_model = env.get(
        f"{ENV_PREFIX}OPENAI_REALTIME_MODEL",
        config.cloud.openai_realtime_model,
    ).strip() or config.cloud.openai_realtime_model
    config.cloud.openai_realtime_voice = env.get(
        f"{ENV_PREFIX}OPENAI_REALTIME_VOICE",
        config.cloud.openai_realtime_voice,
    ).strip() or config.cloud.openai_realtime_voice
    config.cloud.openai_realtime_turn_detection = env.get(
        f"{ENV_PREFIX}OPENAI_REALTIME_TURN_DETECTION",
        config.cloud.openai_realtime_turn_detection,
    ).strip() or config.cloud.openai_realtime_turn_detection
    config.cloud.openai_realtime_turn_eagerness = env.get(
        f"{ENV_PREFIX}OPENAI_REALTIME_TURN_EAGERNESS",
        config.cloud.openai_realtime_turn_eagerness,
    ).strip() or config.cloud.openai_realtime_turn_eagerness
    config.cloud.openai_realtime_local_barge_in_enabled = _parse_bool(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_LOCAL_BARGE_IN_ENABLED"),
        default=config.cloud.openai_realtime_local_barge_in_enabled,
    )
    config.cloud.openai_realtime_interrupt_response = _parse_bool(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_INTERRUPT_RESPONSE"),
        default=config.cloud.openai_realtime_interrupt_response,
    )
    config.cloud.openai_realtime_playback_barge_in_enabled = _parse_bool(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_PLAYBACK_BARGE_IN_ENABLED"),
        default=config.cloud.openai_realtime_playback_barge_in_enabled,
    )
    config.cloud.openai_realtime_playback_barge_in_threshold = _parse_float(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_PLAYBACK_BARGE_IN_THRESHOLD"),
        default=config.cloud.openai_realtime_playback_barge_in_threshold,
    )
    config.cloud.openai_realtime_playback_barge_in_required_ms = _parse_int(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_PLAYBACK_BARGE_IN_REQUIRED_MS"),
        default=config.cloud.openai_realtime_playback_barge_in_required_ms,
    )
    config.cloud.openai_realtime_playback_barge_in_grace_ms = _parse_int(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_PLAYBACK_BARGE_IN_GRACE_MS"),
        default=config.cloud.openai_realtime_playback_barge_in_grace_ms,
    )
    config.cloud.openai_realtime_playback_barge_in_recent_vad_ms = _parse_int(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_PLAYBACK_BARGE_IN_RECENT_VAD_MS"),
        default=config.cloud.openai_realtime_playback_barge_in_recent_vad_ms,
    )
    config.cloud.openai_realtime_playback_barge_in_recent_required_ms = _parse_int(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_PLAYBACK_BARGE_IN_RECENT_REQUIRED_MS"),
        default=config.cloud.openai_realtime_playback_barge_in_recent_required_ms,
    )
    config.cloud.openai_realtime_base_url = env.get(
        f"{ENV_PREFIX}OPENAI_REALTIME_BASE_URL",
        config.cloud.openai_realtime_base_url,
    ).strip() or config.cloud.openai_realtime_base_url
    config.cloud.openai_realtime_audio_sample_rate = _parse_int(
        env.get(f"{ENV_PREFIX}OPENAI_REALTIME_AUDIO_SAMPLE_RATE"),
        default=config.cloud.openai_realtime_audio_sample_rate,
    )

    runtime = config.runtime
    runtime.auto_run = _parse_bool(env.get(f"{ENV_PREFIX}AUTO_RUN"), default=runtime.auto_run)
    runtime.interaction_backend = _parse_interaction_backend(
        env.get(f"{ENV_PREFIX}INTERACTION_BACKEND"),
        default=runtime.interaction_backend,
    )
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
    runtime.audio_init_command = _parse_command(
        env.get(f"{ENV_PREFIX}AUDIO_INIT_COMMAND"),
        default=runtime.audio_init_command,
    )
    runtime.audio_record_command = _parse_command(
        env.get(f"{ENV_PREFIX}AUDIO_RECORD_COMMAND"),
        default=runtime.audio_record_command,
    )
    runtime.audio_input_channels = _parse_int(
        env.get(f"{ENV_PREFIX}AUDIO_INPUT_CHANNELS"),
        default=runtime.audio_input_channels,
    )
    runtime.audio_channel_index = _parse_int(
        env.get(f"{ENV_PREFIX}AUDIO_CHANNEL_INDEX"),
        default=runtime.audio_channel_index,
    )
    runtime.audio_play_command = _parse_command(
        env.get(f"{ENV_PREFIX}AUDIO_PLAY_COMMAND"),
        default=runtime.audio_play_command,
    )
    runtime.audio_output_backend = _parse_audio_output_backend(
        env.get(f"{ENV_PREFIX}AUDIO_OUTPUT_BACKEND"),
        default=_default_audio_output_backend(runtime.audio_play_command),
    )
    runtime.audio_alsa_device = env.get(
        f"{ENV_PREFIX}AUDIO_ALSA_DEVICE",
        runtime.audio_alsa_device,
    ).strip() or runtime.audio_alsa_device
    runtime.audio_alsa_sample_rate = _parse_int(
        env.get(f"{ENV_PREFIX}AUDIO_ALSA_SAMPLE_RATE"),
        default=runtime.audio_alsa_sample_rate,
    )
    runtime.audio_alsa_period_frames = _parse_int(
        env.get(f"{ENV_PREFIX}AUDIO_ALSA_PERIOD_FRAMES"),
        default=runtime.audio_alsa_period_frames,
    )
    runtime.audio_alsa_buffer_frames = _parse_int(
        env.get(f"{ENV_PREFIX}AUDIO_ALSA_BUFFER_FRAMES"),
        default=runtime.audio_alsa_buffer_frames,
    )
    runtime.audio_alsa_keepalive_interval_ms = _parse_int(
        env.get(f"{ENV_PREFIX}AUDIO_ALSA_KEEPALIVE_INTERVAL_MS"),
        default=runtime.audio_alsa_keepalive_interval_ms,
    )
    runtime.audio_save_session_recording = _parse_bool(
        env.get(f"{ENV_PREFIX}AUDIO_SAVE_SESSION_RECORDING"),
        default=runtime.audio_save_session_recording,
    )
    runtime.audio_session_recording_dir = _parse_path(
        env.get(f"{ENV_PREFIX}AUDIO_SESSION_RECORDING_DIR"),
        default=runtime.audio_session_recording_dir,
    )
    runtime.wake_word_enabled = _parse_bool(
        env.get(f"{ENV_PREFIX}WAKE_WORD_ENABLED"),
        default=runtime.wake_word_enabled,
    )
    runtime.follow_up_mode_enabled = _parse_bool(
        env.get(f"{ENV_PREFIX}FOLLOW_UP_MODE_ENABLED"),
        default=runtime.follow_up_mode_enabled,
    )
    runtime.follow_up_listen_timeout_seconds = _parse_float(
        env.get(f"{ENV_PREFIX}FOLLOW_UP_LISTEN_TIMEOUT_SECONDS"),
        default=runtime.follow_up_listen_timeout_seconds,
    )
    runtime.follow_up_max_turns = _parse_int(
        env.get(f"{ENV_PREFIX}FOLLOW_UP_MAX_TURNS"),
        default=runtime.follow_up_max_turns,
    )
    runtime.wake_word_phrase = env.get(f"{ENV_PREFIX}WAKE_WORD_PHRASE", runtime.wake_word_phrase).strip()
    runtime.wake_word_model = env.get(f"{ENV_PREFIX}WAKE_WORD_MODEL", runtime.wake_word_model).strip()
    runtime.wake_word_threshold = _parse_float(
        env.get(f"{ENV_PREFIX}WAKE_WORD_THRESHOLD"),
        default=runtime.wake_word_threshold,
    )
    runtime.wake_lookback_seconds = _parse_float(
        env.get(f"{ENV_PREFIX}WAKE_LOOKBACK_SECONDS"),
        default=runtime.wake_lookback_seconds,
    )
    if runtime.wake_lookback_seconds <= 0:
        runtime.wake_lookback_seconds = 0.8
    runtime.audio_input_channels = max(1, runtime.audio_input_channels)
    runtime.audio_channel_index = max(0, runtime.audio_channel_index)
    if runtime.audio_channel_index >= runtime.audio_input_channels:
        runtime.audio_channel_index = runtime.audio_input_channels - 1
    if runtime.wake_word_threshold <= 0:
        runtime.wake_word_threshold = 0.5
    elif runtime.wake_word_threshold > 1.0:
        runtime.wake_word_threshold = 1.0
    if runtime.follow_up_listen_timeout_seconds <= 0:
        runtime.follow_up_listen_timeout_seconds = 5.0
    runtime.follow_up_max_turns = max(1, runtime.follow_up_max_turns)
    runtime.language_mode = _parse_language_mode(
        env.get(f"{ENV_PREFIX}LANGUAGE_MODE"),
        default=runtime.language_mode,
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
    ui = config.ui
    ui.backend = _parse_ui_backend(
        env.get(f"{ENV_PREFIX}UI_BACKEND"),
        default=ui.backend,
    )
    ui.idle_sleep_seconds = _parse_float(
        env.get(f"{ENV_PREFIX}UI_IDLE_SLEEP_SECONDS"),
        default=ui.idle_sleep_seconds,
    )
    ui.sleeping_eyes_grace_seconds = _parse_float(
        env.get(f"{ENV_PREFIX}UI_SLEEPING_EYES_GRACE_SECONDS"),
        default=ui.sleeping_eyes_grace_seconds,
    )
    ui.show_text_overlay = _parse_bool(
        env.get(f"{ENV_PREFIX}UI_SHOW_TEXT_OVERLAY"),
        default=ui.show_text_overlay,
    )
    ui.sleep_command = _parse_command(
        env.get(f"{ENV_PREFIX}UI_SLEEP_COMMAND"),
        default=ui.sleep_command,
    )
    ui.wake_command = _parse_command(
        env.get(f"{ENV_PREFIX}UI_WAKE_COMMAND"),
        default=ui.wake_command,
    )
    ui.browser_host = env.get(f"{ENV_PREFIX}UI_BROWSER_HOST", ui.browser_host).strip() or ui.browser_host
    ui.browser_http_port = _parse_int(
        env.get(f"{ENV_PREFIX}UI_BROWSER_HTTP_PORT"),
        default=ui.browser_http_port,
    )
    ui.browser_ws_port = _parse_int(
        env.get(f"{ENV_PREFIX}UI_BROWSER_WS_PORT"),
        default=ui.browser_ws_port,
    )
    ui.browser_launch_mode = _parse_browser_launch_mode(
        env.get(f"{ENV_PREFIX}UI_BROWSER_LAUNCH_MODE"),
        default=ui.browser_launch_mode,
    )
    ui.browser_executable = env.get(
        f"{ENV_PREFIX}UI_BROWSER_EXECUTABLE",
        ui.browser_executable,
    ).strip()
    ui.browser_profile_dir = _parse_optional_path(
        env.get(f"{ENV_PREFIX}UI_BROWSER_PROFILE_DIR"),
    )
    ui.browser_extra_args = _parse_command(
        env.get(f"{ENV_PREFIX}UI_BROWSER_EXTRA_ARGS"),
        default=ui.browser_extra_args,
    )
    ui.browser_state_path = _parse_optional_path(
        env.get(f"{ENV_PREFIX}UI_BROWSER_STATE_PATH"),
    )
    ui.face_idle_enabled = _parse_bool(
        env.get(f"{ENV_PREFIX}UI_FACE_IDLE_ENABLED"),
        default=ui.face_idle_enabled,
    )
    ui.face_idle_frequency = _parse_float(
        env.get(f"{ENV_PREFIX}UI_FACE_IDLE_FREQUENCY"),
        default=ui.face_idle_frequency,
    )
    ui.face_idle_intensity = _parse_float(
        env.get(f"{ENV_PREFIX}UI_FACE_IDLE_INTENSITY"),
        default=ui.face_idle_intensity,
    )
    ui.face_idle_pause_randomness = _parse_float(
        env.get(f"{ENV_PREFIX}UI_FACE_IDLE_PAUSE_RANDOMNESS"),
        default=ui.face_idle_pause_randomness,
    )
    ui.face_secondary_micro_motion = _parse_bool(
        env.get(f"{ENV_PREFIX}UI_FACE_SECONDARY_MICRO_MOTION"),
        default=ui.face_secondary_micro_motion,
    )
    ui.face_idle_behaviors = _parse_csv_tuple(
        env.get(f"{ENV_PREFIX}UI_FACE_IDLE_BEHAVIORS"),
        default=ui.face_idle_behaviors,
    )
    _validate_runtime_config(runtime)
    _validate_cloud_config(config.cloud, runtime=runtime)
    _validate_ui_config(ui)

    return config


def _validate_runtime_config(runtime: RuntimeConfig) -> None:
    """Reject obviously incomplete wake-word configurations early."""

    if runtime.audio_input_channels <= 0:
        raise ValueError("AI_COMPANION_AUDIO_INPUT_CHANNELS must be greater than zero")
    if runtime.audio_channel_index < 0:
        raise ValueError("AI_COMPANION_AUDIO_CHANNEL_INDEX must be zero or greater")
    if runtime.audio_channel_index >= runtime.audio_input_channels:
        raise ValueError("AI_COMPANION_AUDIO_CHANNEL_INDEX must be smaller than AI_COMPANION_AUDIO_INPUT_CHANNELS")
    if runtime.audio_output_backend == "alsa_persistent":
        if not runtime.audio_alsa_device.strip():
            raise ValueError("AI_COMPANION_AUDIO_ALSA_DEVICE must be configured")
        if runtime.audio_alsa_sample_rate <= 0:
            raise ValueError("AI_COMPANION_AUDIO_ALSA_SAMPLE_RATE must be greater than zero")
        if runtime.audio_alsa_period_frames <= 0:
            raise ValueError("AI_COMPANION_AUDIO_ALSA_PERIOD_FRAMES must be greater than zero")
        if runtime.audio_alsa_buffer_frames < runtime.audio_alsa_period_frames:
            raise ValueError("AI_COMPANION_AUDIO_ALSA_BUFFER_FRAMES must be greater than or equal to period frames")
        if runtime.audio_alsa_keepalive_interval_ms <= 0:
            raise ValueError("AI_COMPANION_AUDIO_ALSA_KEEPALIVE_INTERVAL_MS must be greater than zero")
    if not runtime.wake_word_enabled:
        return
    if not runtime.wake_word_phrase.strip():
        raise ValueError("wake word detection is enabled but AI_COMPANION_WAKE_WORD_PHRASE is not configured")
    if not runtime.wake_word_model.strip():
        raise ValueError("wake word detection is enabled but AI_COMPANION_WAKE_WORD_MODEL is not configured")


def _validate_cloud_config(cloud: CloudConfig, *, runtime: RuntimeConfig) -> None:
    """Reject incomplete non-mock cloud configuration."""

    if runtime.use_mock_ai or not cloud.enabled:
        return
    if (cloud.provider_name or "").strip().lower() != "openai":
        raise ValueError("only the 'openai' cloud provider is currently supported for real cloud execution")
    if not cloud.openai_api_key.strip():
        raise ValueError("cloud AI is enabled but AI_COMPANION_OPENAI_API_KEY is not configured")
    if runtime.interaction_backend != "openai_realtime" and not cloud.openai_response_model.strip():
        raise ValueError("cloud AI is enabled but AI_COMPANION_OPENAI_RESPONSE_MODEL is not configured")
    if cloud.openai_timeout_seconds <= 0:
        raise ValueError("AI_COMPANION_OPENAI_TIMEOUT_SECONDS must be greater than zero")
    if cloud.openai_reply_max_output_tokens <= 0:
        raise ValueError("AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS must be greater than zero")
    if cloud.openai_realtime_audio_sample_rate <= 0:
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_AUDIO_SAMPLE_RATE must be greater than zero")
    if not cloud.openai_realtime_model.strip():
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_MODEL must not be empty")
    if not cloud.openai_realtime_voice.strip():
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_VOICE must not be empty")
    if not cloud.openai_realtime_base_url.strip():
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_BASE_URL must not be empty")
    if cloud.openai_realtime_turn_detection not in {"server_vad", "semantic_vad", "none"}:
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_TURN_DETECTION must be server_vad, semantic_vad, or none")
    if cloud.openai_realtime_turn_eagerness not in {"auto", "low", "medium", "high"}:
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_TURN_EAGERNESS must be auto, low, medium, or high")
    if cloud.openai_realtime_playback_barge_in_threshold <= 0:
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_PLAYBACK_BARGE_IN_THRESHOLD must be greater than zero")
    if cloud.openai_realtime_playback_barge_in_required_ms <= 0:
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_PLAYBACK_BARGE_IN_REQUIRED_MS must be greater than zero")
    if cloud.openai_realtime_playback_barge_in_grace_ms < 0:
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_PLAYBACK_BARGE_IN_GRACE_MS must be zero or greater")
    if cloud.openai_realtime_playback_barge_in_recent_vad_ms <= 0:
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_PLAYBACK_BARGE_IN_RECENT_VAD_MS must be greater than zero")
    if cloud.openai_realtime_playback_barge_in_recent_required_ms <= 0:
        raise ValueError("AI_COMPANION_OPENAI_REALTIME_PLAYBACK_BARGE_IN_RECENT_REQUIRED_MS must be greater than zero")


def _validate_ui_config(ui: UiConfig) -> None:
    if ui.idle_sleep_seconds < 0:
        raise ValueError("AI_COMPANION_UI_IDLE_SLEEP_SECONDS must be zero or greater")
    if ui.sleeping_eyes_grace_seconds < 0:
        raise ValueError("AI_COMPANION_UI_SLEEPING_EYES_GRACE_SECONDS must be zero or greater")
    if not ui.browser_host.strip():
        raise ValueError("AI_COMPANION_UI_BROWSER_HOST must not be empty")
    if ui.browser_http_port < 0:
        raise ValueError("AI_COMPANION_UI_BROWSER_HTTP_PORT must be zero or greater")
    if ui.browser_ws_port < 0:
        raise ValueError("AI_COMPANION_UI_BROWSER_WS_PORT must be zero or greater")
    if ui.face_idle_frequency < 0:
        raise ValueError("AI_COMPANION_UI_FACE_IDLE_FREQUENCY must be zero or greater")
    if not 0 <= ui.face_idle_intensity <= 1:
        raise ValueError("AI_COMPANION_UI_FACE_IDLE_INTENSITY must be between zero and one")
    if not 0 <= ui.face_idle_pause_randomness <= 1:
        raise ValueError("AI_COMPANION_UI_FACE_IDLE_PAUSE_RANDOMNESS must be between zero and one")
    if any(not item.strip() for item in ui.face_idle_behaviors):
        raise ValueError("AI_COMPANION_UI_FACE_IDLE_BEHAVIORS must not contain empty values")


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


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    return float(value.strip())


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


def _parse_ui_backend(
    value: str | None,
    default: Literal["mock", "browser"],
) -> Literal["mock", "browser"]:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"mock", "browser"}:
        return normalized
    raise ValueError("AI_COMPANION_UI_BACKEND must be one of 'mock' or 'browser'")


def _parse_browser_launch_mode(
    value: str | None,
    default: Literal["kiosk", "windowed", "connect_only"],
) -> Literal["kiosk", "windowed", "connect_only"]:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"kiosk", "windowed", "connect_only"}:
        return normalized
    raise ValueError(
        "AI_COMPANION_UI_BROWSER_LAUNCH_MODE must be one of 'kiosk', 'windowed', or 'connect_only'"
    )


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


def _parse_interaction_backend(
    value: str | None,
    default: Literal["turn_based", "openai_realtime"],
) -> Literal["turn_based", "openai_realtime"]:
    if value in {"turn_based", "openai_realtime"}:
        return value
    return default


def _parse_language_mode(
    value: str | None,
    default: Literal["auto", "en", "de", "id"],
) -> Literal["auto", "en", "de", "id"]:
    if value in {"auto", "en", "de", "id"}:
        return value
    return default


def _parse_audio_output_backend(
    value: str | None,
    default: Literal["command", "alsa_persistent"],
) -> Literal["command", "alsa_persistent"]:
    if value in {"command", "alsa_persistent"}:
        return value
    return default

def _default_audio_output_backend(command_template: tuple[str, ...]) -> Literal["command", "alsa_persistent"]:
    if sys.platform != "darwin" and command_template and Path(command_template[0]).name == "aplay":
        return "alsa_persistent"
    return "command"

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
    provider_name: str | None = None


@dataclass(slots=True)
class AppConfig:
    """Top-level application configuration placeholder."""

    default_language: Language = Language.ENGLISH
    paths: PathConfig = field(default_factory=PathConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)


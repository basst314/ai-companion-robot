"""Application entry point for the AI companion robot."""

from orchestrator.service import OrchestratorService
from orchestrator.state import OrchestratorState
from shared.config import AppConfig


def main() -> int:
    """Create the top-level application objects and exit."""
    config = AppConfig()
    state = OrchestratorState.initial()
    _service = OrchestratorService(config=config, state=state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

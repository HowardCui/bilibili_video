"""Application factory and local launch entry point."""

from collections.abc import Callable
from pathlib import Path

from shiny import App

from ranking_collector.config import DATABASE_PATH
from ranking_collector.repository import initialize_database

from .config import HOST, PORT
from .layout import build_app_ui
from .ranking.server import register_ranking_server
from .summary.server import register_summary_server
from .summary.service import SummaryTaskService

STATIC_ASSETS = Path(__file__).parent / "www"


def create_app(
    database_path: str | Path | None = None,
    summary_runner: Callable[..., object] | None = None,
) -> App:
    """Create the web application shell with injectable service dependencies."""
    resolved_database_path = (
        Path(database_path) if database_path is not None else DATABASE_PATH
    )
    initialize_database(resolved_database_path)
    if summary_runner is None:
        from summarization.video_summary import summarize_bilibili_video

        resolved_summary_runner = summarize_bilibili_video
    else:
        resolved_summary_runner = summary_runner
    summary_service = SummaryTaskService(
        resolved_database_path,
        resolved_summary_runner,
    )
    summary_service.start()

    def server(input, output, session):
        register_ranking_server(input, output, session, resolved_database_path)
        register_summary_server(input, output, session, summary_service)

    try:
        app = App(build_app_ui(), server, static_assets=STATIC_ASSETS)
        app.on_shutdown(summary_service.shutdown)
    except Exception:
        summary_service.shutdown()
        raise
    return app


def main() -> None:
    """Run the application on its fixed local-only address."""
    create_app().run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()

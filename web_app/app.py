"""Application factory and local launch entry point."""

from collections.abc import Callable
from pathlib import Path

from shiny import App

from ranking_collector.config import DATABASE_PATH
from ranking_collector.repository import initialize_database
from uploader_analysis.repository import (
    initialize_uploader_database,
    sync_ranked_uploaders,
)
from uploader_analysis.service import UploaderCollectionService

from .config import HOST, PORT
from .layout import build_app_ui
from .ranking.server import register_ranking_server
from .summary.server import register_summary_server
from .summary.service import SummaryTaskService
from .uploader.server import register_uploader_server

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
    initialize_uploader_database(resolved_database_path)
    sync_ranked_uploaders(resolved_database_path)
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
    uploader_service = UploaderCollectionService(resolved_database_path)

    def server(input, output, session):
        register_ranking_server(input, output, session, resolved_database_path)
        register_summary_server(input, output, session, summary_service)
        if getattr(input, "uploader_collect", None) is not None:
            register_uploader_server(
                input,
                output,
                session,
                resolved_database_path,
                uploader_service,
            )

    def shutdown_services():
        summary_service.shutdown()
        uploader_service.shutdown()

    try:
        app = App(build_app_ui(), server, static_assets=STATIC_ASSETS)
        app.on_shutdown(shutdown_services)
    except Exception:
        shutdown_services()
        raise
    return app


def main() -> None:
    """Run the application on its fixed local-only address."""
    create_app().run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()

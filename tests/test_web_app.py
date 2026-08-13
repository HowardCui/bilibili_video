import os
import subprocess
import sys
from pathlib import Path

from htmltools import Tag

from web_app.app import create_app
from web_app.layout import build_app_ui

_WWW = Path(__file__).parents[1] / "web_app" / "www"


def test_build_app_ui_contains_two_main_entries():
    markup = str(build_app_ui())
    assert "排行榜" in markup
    assert "视频总结" in markup


def test_build_app_ui_exposes_the_responsive_dashboard_shell_and_stylesheets():
    """Removing the shell contract would disconnect the responsive CSS."""
    markup = str(build_app_ui())

    assert 'class="app-shell"' in markup
    assert 'class="side-navigation"' in markup
    assert 'class="dashboard-navigation"' in markup
    assert 'href="tokens.css"' in markup
    assert 'href="layout.css"' in markup
    assert 'href="animations.css"' in markup
    assert "<style" not in markup


def test_build_app_ui_declares_its_tag_boundary():
    """Callers should retain the declared htmltools boundary from Task 1."""
    assert build_app_ui.__annotations__["return"] is Tag


def test_build_app_ui_selects_the_ranking_panel_by_its_stable_value():
    """Decorating a tab label must not prevent its default panel from opening."""
    markup = str(build_app_ui())

    assert 'data-value="排行榜" role="tab" class="nav-link active"' in markup
    assert 'class="tab-pane active" role="tabpanel" data-value="排行榜"' in markup


def test_accessible_navigation_name_belongs_to_the_actual_tab_control():
    """Branding must not claim the navigation name used by the tab controls."""
    markup = str(build_app_ui())

    assert '<aside class="side-navigation" aria-label="应用品牌">' in markup
    assert '<nav class="dashboard-navigation" aria-label="主要功能">' in markup


def test_component_styles_use_tokens_for_palette_and_motion_values():
    """Theme and motion tuning must stay centralized in the token layer."""
    tokens = (_WWW / "tokens.css").read_text(encoding="utf-8")
    layout = (_WWW / "layout.css").read_text(encoding="utf-8")
    animations = (_WWW / "animations.css").read_text(encoding="utf-8")

    assert "--color-navigation-glass:" in tokens
    assert "--color-gold-transparent:" in tokens
    assert "--motion-spin:" in tokens
    assert "--motion-shake:" in tokens
    assert "rgba(" not in layout
    assert "rgba(" not in animations
    assert "900ms" not in animations
    assert "460ms" not in animations


def test_stylesheets_keep_desktop_mobile_and_reduced_motion_contracts():
    """Removing a responsive or reduced-motion rule must fail ordinary tests."""
    layout = (_WWW / "layout.css").read_text(encoding="utf-8")
    animations = (_WWW / "animations.css").read_text(encoding="utf-8")

    assert ".app-shell {\n  position: relative;" in layout
    assert "@media (max-width: 1050px)" in layout
    assert "@media (max-width: 720px)" in layout
    assert "position: fixed;\n    z-index: 1000;" in layout
    assert "padding: var(--space-4) var(--space-4) 104px;" in layout
    assert "overflow-x: auto;" in layout
    assert "@media (prefers-reduced-motion: reduce)" in animations
    reduced_states = ".task-animation.is-processing,\n  .task-animation.is-succeeded"
    assert reduced_states in animations
    assert "animation: none;\n    transform: none;" in animations


def test_create_app_returns_shiny_application(tmp_path):
    app = create_app(
        database_path=tmp_path / "ranking.db",
        summary_runner=lambda *_args, **_kwargs: None,
    )
    assert app is not None


def test_module_entrypoint_runs_on_fixed_local_address(tmp_path):
    """The documented module command must reach Shiny on localhost only."""
    project_root = Path(__file__).resolve().parents[1]
    (tmp_path / "sitecustomize.py").write_text(
        """
from shiny import App


def controlled_run(self, *, host, port, **kwargs):
    print(f"CONTROLLED_SHINY_RUN={host}:{port}")


App.run = controlled_run
""".strip(),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    python_path = [str(tmp_path), str(project_root)]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)

    completed = subprocess.run(
        [sys.executable, "-m", "web_app.app"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "CONTROLLED_SHINY_RUN=127.0.0.1:8000" in completed.stdout
    assert "0.0.0.0" not in completed.stdout

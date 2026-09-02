import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

_PROJECT_ROOT = Path(__file__).parents[1]


def test_built_wheel_contains_web_static_css(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(tmp_path),
            str(_PROJECT_ROOT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    wheel_paths = list(tmp_path.glob("*.whl"))
    assert len(wheel_paths) == 1
    with ZipFile(wheel_paths[0]) as wheel:
        wheel_entries = set(wheel.namelist())

    assert {
        "app_logging/__init__.py",
        "app_logging/config.py",
        "app_logging/sanitization.py",
        "web_app/www/animations.css",
        "web_app/www/layout.css",
        "web_app/www/tokens.css",
    } <= wheel_entries

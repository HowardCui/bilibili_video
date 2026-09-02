import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "manage_ranking_task.ps1"
TASK_NAMESPACE = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_windows_task_preview_uses_project_venv_and_beijing_triggers(tmp_path):
    python_path = tmp_path / "python.exe"
    python_path.touch()
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_PATH),
            "-Action",
            "Preview",
            "-ProjectRoot",
            str(PROJECT_ROOT),
            "-PythonPath",
            str(python_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    root = ET.fromstring(completed.stdout.lstrip("\ufeff"))
    boundaries = [
        node.text
        for node in root.findall(".//task:StartBoundary", TASK_NAMESPACE)
    ]
    assert len(boundaries) == 4
    assert {value[11:19] for value in boundaries} == {
        "00:00:00",
        "06:00:00",
        "12:00:00",
        "18:00:00",
    }
    assert all(value.endswith("+08:00") for value in boundaries)
    command = root.findtext(".//task:Exec/task:Command", namespaces=TASK_NAMESPACE)
    arguments = root.findtext(
        ".//task:Exec/task:Arguments", namespaces=TASK_NAMESPACE
    )
    working_directory = root.findtext(
        ".//task:Exec/task:WorkingDirectory", namespaces=TASK_NAMESPACE
    )
    assert command == str(python_path)
    assert arguments == "-m automation.ranking_once --json"
    assert working_directory == str(PROJECT_ROOT)


def test_windows_task_preview_defaults_to_repository_root(tmp_path):
    python_path = tmp_path / "python.exe"
    python_path.touch()
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT_PATH),
            "-Action",
            "Preview",
            "-PythonPath",
            str(python_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not completed.stdout.lstrip().startswith("<?xml")
    root = ET.fromstring(completed.stdout.lstrip("\ufeff"))
    working_directory = root.findtext(
        ".//task:Exec/task:WorkingDirectory", namespaces=TASK_NAMESPACE
    )
    assert working_directory == str(PROJECT_ROOT)

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "manage_ranking_task.ps1"
TASK_NAMESPACE = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _existing_python():
    local = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if local.exists():
        return local
    return PROJECT_ROOT.parents[1] / ".venv" / "Scripts" / "python.exe"


def test_windows_task_preview_uses_project_venv_and_beijing_triggers():
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
            str(_existing_python()),
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
    assert command == str(_existing_python())
    assert arguments == "-m automation.ranking_once --json"
    assert working_directory == str(PROJECT_ROOT)

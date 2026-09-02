import json
from io import StringIO

from app_logging import configure_logging, get_logger, log_event, shutdown_logging


def _read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_structured_log_uses_beijing_time_and_stable_fields(tmp_path):
    configure_logging("web", log_dir=tmp_path, stream=StringIO())
    logger = get_logger("web.service")

    log_event(
        logger,
        "INFO",
        "web_started",
        "Web 服务已启动",
        task_type="web",
        task_id="task-1",
        run_id=7,
        partition="全站",
        duration_ms=12,
    )
    shutdown_logging()

    record = _read_json_lines(tmp_path / "web.log")[0]
    assert record == {
        "timestamp": record["timestamp"],
        "level": "INFO",
        "module": "web.service",
        "event": "web_started",
        "message": "Web 服务已启动",
        "task_type": "web",
        "task_id": "task-1",
        "run_id": 7,
        "partition": "全站",
        "duration_ms": 12,
    }
    assert record["timestamp"].endswith("+08:00")


def test_log_redacts_credentials_headers_signatures_and_private_paths(tmp_path):
    configure_logging("automation", log_dir=tmp_path, stream=StringIO())
    logger = get_logger("automation.ranking")

    log_event(
        logger,
        "ERROR",
        "request_failed",
        (
            "Cookie: SESSDATA=secret Authorization: Bearer abc "
            "api_key=my-key w_rid=signature "
            "C:\\Users\\alice\\project\\.secrets\\cookies.txt"
        ),
    )
    shutdown_logging()

    message = _read_json_lines(tmp_path / "automation.log")[0]["message"]
    assert "secret" not in message
    assert "my-key" not in message
    assert "signature" not in message
    assert "alice" not in message
    assert "[REDACTED]" in message


def test_repeated_configuration_does_not_duplicate_records(tmp_path):
    stream = StringIO()
    configure_logging("ranking", log_dir=tmp_path, stream=stream)
    configure_logging("ranking", log_dir=tmp_path, stream=stream)

    log_event(get_logger("ranking.pipeline"), "INFO", "started", "开始")
    shutdown_logging()

    assert len(_read_json_lines(tmp_path / "ranking.log")) == 1


def test_log_file_rotates_at_configured_size(tmp_path):
    configure_logging(
        "web",
        log_dir=tmp_path,
        max_bytes=220,
        backup_count=2,
        stream=StringIO(),
    )
    logger = get_logger("web.service")
    for index in range(8):
        log_event(logger, "INFO", "heartbeat", f"第 {index} 次状态记录")
    shutdown_logging()

    assert (tmp_path / "web.log").exists()
    assert (tmp_path / "web.log.1").exists()
    assert len(list(tmp_path.glob("web.log*"))) <= 3


def test_unwritable_log_directory_falls_back_to_console(monkeypatch, tmp_path):
    def fail_mkdir(*_args, **_kwargs):
        raise OSError("read-only")

    monkeypatch.setattr("app_logging.config.Path.mkdir", fail_mkdir)
    stream = StringIO()

    result = configure_logging("web", log_dir=tmp_path, stream=stream)
    log_event(get_logger("web.service"), "WARNING", "fallback", "文件不可写")
    shutdown_logging()

    assert result["file_enabled"] is False
    assert '"event": "fallback"' in stream.getvalue()


def test_handler_failure_does_not_escape_into_business_code(monkeypatch, tmp_path):
    configure_logging("web", log_dir=tmp_path, stream=StringIO())
    logger = get_logger("web.service")
    file_handler = logger.parent.handlers[1]

    def fail_emit(_record):
        raise RuntimeError("disk handler failed")

    monkeypatch.setattr(file_handler, "emit", fail_emit)

    assert log_event(logger, "ERROR", "write_failed", "日志写入失败") is False
    shutdown_logging()

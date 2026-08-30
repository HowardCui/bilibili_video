"""将排行榜单次采集结果转换为安全输出。"""

import json
import re

_SENSITIVE_PATTERN = re.compile(
    r"cookie|api[_ -]?key|authorization|\.secrets|"
    r"[A-Za-z]:[\\/]|/(?:home|users|etc)/",
    re.IGNORECASE,
)


def safe_error_message(message):
    if not message:
        return None
    text = str(message).strip()
    if _SENSITIVE_PATTERN.search(text):
        return "采集请求失败，请检查网络或本地凭据"
    return text[:300]


def format_json_report(result):
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def format_text_report(result):
    lines = [f"排行榜单次采集：{result['status']}"]
    if result.get("run_id") is not None:
        lines.append(f"任务 ID：{result['run_id']}")
    if result.get("started_at"):
        lines.append(f"开始时间：{result['started_at']}")
    if result.get("finished_at"):
        lines.append(f"结束时间：{result['finished_at']}")
    if result.get("duration_seconds") is not None:
        lines.append(f"总耗时：{result['duration_seconds']:.1f} 秒")
    for item in result.get("partitions", []):
        state = "成功" if item["succeeded"] else "失败"
        line = f"- {item['partition']}：{state}，保存 {item['item_count']} 条"
        if item.get("error"):
            line += f"；{item['error']}"
        lines.append(line)
    if result.get("message"):
        lines.append(result["message"])
    return "\n".join(lines)


__all__ = ["format_json_report", "format_text_report", "safe_error_message"]

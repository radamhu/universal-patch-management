from datetime import datetime

_ESCAPES = str.maketrans({"\\": "\\\\", "\"": "\\\"", "\n": "\\n"})

_LABEL_KEYS = ("id", "group", "host", "kind", "name", "current", "latest", "status", "stale")


def _label_value(v):
    return (v or "").translate(_ESCAPES)


def _bool_label_value(v):
    return "true" if v else "false"


def _labels(component):
    pairs = ",".join(
        f'{k}="{_bool_label_value(component.get(k)) if k == "stale" else _label_value(component.get(k))}"'
        for k in _LABEL_KEYS
    )
    return pairs


def render_prometheus(doc):
    if not doc:
        return b""
    lines = [
        "# HELP upm_component_status Component status, one series per component (value always 1).",
        "# TYPE upm_component_status gauge",
    ]
    for c in doc.get("components") or []:
        lines.append(f"upm_component_status{{{_labels(c)}}} 1")

    try:
        ts = datetime.fromisoformat(doc["generated_at"]).timestamp()
    except (TypeError, KeyError, ValueError):
        ts = None
    if ts is not None:
        lines.append("# HELP upm_generated_timestamp_seconds Unix time the status doc was generated.")
        lines.append("# TYPE upm_generated_timestamp_seconds gauge")
        lines.append(f"upm_generated_timestamp_seconds {ts}")

    return ("\n".join(lines) + "\n").encode()

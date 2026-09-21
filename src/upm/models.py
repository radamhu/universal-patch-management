from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .versions import backup_status, status_for


@dataclass
class Component:
    id: str
    group: str  # "core" | "app"
    host: str
    kind: str
    name: str
    current: str | None
    latest: str | None
    status: str  # ok | outdated | unknown | error
    checked_at: str
    stale: bool = False
    error: str | None = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def version_component(*, id, group, host, kind, name, current, latest, now):
    return Component(id=id, group=group, host=host, kind=kind, name=name,
                     current=current, latest=latest,
                     status=status_for(current, latest), checked_at=_iso(now))


def backup_component(*, id, group, host, name, last_ts, max_age_h, now):
    current = _iso(datetime.fromtimestamp(last_ts, timezone.utc)) if last_ts else None
    return Component(id=id, group=group, host=host, kind="backup", name=name,
                     current=current, latest=None,
                     status=backup_status(last_ts, int(now.timestamp()), max_age_h),
                     checked_at=_iso(now))


def error_component(*, id, group, host, kind, name, error, now):
    return Component(id=id, group=group, host=host, kind=kind, name=name,
                     current=None, latest=None, status="error",
                     checked_at=_iso(now), error=error)

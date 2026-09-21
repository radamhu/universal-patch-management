from datetime import datetime, timezone

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class FakeResolver:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def latest(self, key):
        return self.mapping.get(key)

import json
import os
from pathlib import Path


class Store:
    def __init__(self, data_dir):
        self._dir = Path(data_dir)
        self._path = self._dir / "status.json"

    def load(self):
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def save(self, doc):
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2))
        os.replace(tmp, self._path)

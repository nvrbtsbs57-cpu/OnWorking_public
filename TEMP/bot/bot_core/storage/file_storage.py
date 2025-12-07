import json
import pathlib
from typing import Any, Dict, List

from .abstract import AbstractStorage


class FileStorage(AbstractStorage):
    def __init__(self, base_path: str) -> None:
        self.base_path = pathlib.Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.meta_file = self.base_path / "meta.json"
        if not self.meta_file.exists():
            self.meta_file.write_text("{}", encoding="utf-8")

    def _load_meta(self) -> Dict[str, Any]:
        return json.loads(self.meta_file.read_text(encoding="utf-8"))

    def _save_meta(self, data: Dict[str, Any]) -> None:
        self.meta_file.write_text(json.dumps(data), encoding="utf-8")

    def save_events(self, chain: str, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        chain_file = self.base_path / f"{chain}.log"
        with chain_file.open("a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

    def get_last_block(self, chain: str) -> int:
        meta = self._load_meta()
        return int(meta.get(chain, 0))

    def set_last_block(self, chain: str, block: int) -> None:
        meta = self._load_meta()
        meta[chain] = block
        self._save_meta(meta)

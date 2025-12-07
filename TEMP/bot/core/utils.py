import json
import pathlib
from typing import Any, Dict


def load_json(path: str) -> Dict[str, Any]:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_main_config(base_dir: str = ".") -> Dict[str, Any]:
    base = pathlib.Path(base_dir)
    config_path = base / "config" / "config.json"
    return load_json(str(config_path))

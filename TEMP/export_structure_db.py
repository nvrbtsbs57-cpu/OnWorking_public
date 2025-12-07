#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

# Logging – on essaye d'utiliser le logger du projet, sinon fallback basique
try:
    from bot.core.logging import get_logger  # type: ignore
except Exception:  # pragma: no cover
    import logging

    logging.basicConfig(level=logging.INFO)

    def get_logger(name: str):
        return logging.getLogger(name)


log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "bot_structure.db"
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "TEMP" / "PROJECT_SNAPSHOT_M10" / "data"


@dataclass
class ColumnInfo:
    name: str
    type: str
    not_null: bool
    default: Any
    pk: bool


@dataclass
class TableInfo:
    name: str
    columns: List[ColumnInfo]
    row_count: int


def _introspect_db(db_path: Path) -> Dict[str, Any]:
    """Introspection *générique* de la DB sqlite (sans dépendre du schéma)."""

    if not db_path.exists():
        raise FileNotFoundError(f"DB introuvable : {db_path}")

    log.info("Introspection de la DB structure : %s", db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    tables = [row[0] for row in cur.fetchall()]

    tables_info: List[TableInfo] = []

    for tbl in tables:
        # Colonnes
        cur.execute(f"PRAGMA table_info({tbl})")
        cols: List[ColumnInfo] = []
        for row in cur.fetchall():
            cols.append(
                ColumnInfo(
                    name=row["name"],
                    type=row["type"],
                    not_null=bool(row["notnull"]),
                    default=row["dflt_value"],
                    pk=bool(row["pk"]),
                )
            )

        # Row count
        try:
            cur.execute(f"SELECT COUNT(*) AS c FROM {tbl}")
            rc = cur.fetchone()
            row_count = int(rc["c"]) if rc is not None else 0
        except sqlite3.Error:
            row_count = -1  # au cas où table spéciale

        tables_info.append(TableInfo(name=tbl, columns=cols, row_count=row_count))

    conn.close()

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "db_path": str(db_path),
        "tables": [asdict(t) for t in tables_info],
    }


def _write_snapshot(snapshot_dir: Path, payload: Dict[str, Any]) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    json_path = snapshot_dir / "bot_structure_snapshot.json"
    md_path = snapshot_dir / "bot_structure_snapshot.md"

    # JSON brut
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    # Version Markdown lisible
    lines: List[str] = []
    lines.append("# BOT_GODMODE – Snapshot DB structure\n")
    lines.append(f"- Generated at : {payload['generated_at']}")
    lines.append(f"- DB path      : `{payload['db_path']}`\n")
    lines.append("---\n")

    for t in payload["tables"]:
        lines.append(f"## Table `{t['name']}`")
        lines.append(f"- Row count : {t['row_count']}")
        lines.append("")
        lines.append("| # | Column | Type | Not Null | PK | Default |")
        lines.append("|---|--------|------|----------|----|---------|")
        for idx, col in enumerate(t["columns"], start=1):
            not_null = "yes" if col["not_null"] else "no"
            pk = "yes" if col["pk"] else "no"
            default = col["default"]
            lines.append(
                f"| {idx} | {col['name']} | {col['type']} | "
                f"{not_null} | {pk} | {default} |"
            )
        lines.append("")

    with md_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info("Snapshot JSON     : %s", json_path)
    log.info("Snapshot Markdown : %s", md_path)


def main(snapshot_dir: Path | None = None) -> None:
    snapshot_dir = snapshot_dir or DEFAULT_SNAPSHOT_DIR
    payload = _introspect_db(DB_PATH)
    _write_snapshot(snapshot_dir, payload)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Export générique de la DB bot_structure.db vers TEMP/PROJECT_SNAPSHOT_M10."
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(DEFAULT_SNAPSHOT_DIR),
        help="Dossier de sortie (par défaut: TEMP/PROJECT_SNAPSHOT_M10/data).",
    )
    args = parser.parse_args()
    main(Path(args.out))

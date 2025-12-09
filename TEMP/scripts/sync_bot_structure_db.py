#!/usr/bin/env python3
"""
sync_bot_structure_db.py

Met à jour bot_structure.db en ajoutant / mettant à jour les entrées de la table `items`
en fonction des fichiers présents sur le disque dans le dossier du bot.

- Ne crée pas la table `items`
- Ne supprime pas d'entrées existantes
- N'agit QUE sur la DB du dossier BOT (par défaut)

Utilisation (depuis le dossier BOT_GODMODE/BOT_GODMODE) :

    (venv) python scripts/sync_bot_structure_db.py

Optionnel :

    (venv) python scripts/sync_bot_structure_db.py --root /chemin/vers/BOT_GODMODE/BOT_GODMODE --db /chemin/vers/bot_structure.db
"""

import os
import sys
import argparse
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Tuple, Optional

# Dossiers à ignorer
SKIP_DIR_NAMES = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    ".mypy_cache",
}


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def table_items_exists(conn: sqlite3.Connection) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='items';"
    )
    return cur.fetchone() is not None


def scan_repo(root_dir: Path) -> Iterable[Tuple[str, str, str, int, Optional[int], Optional[int], Optional[str]]]:
    """
    Génère des tuples :
        (full_path, relative_path, name, is_dir, size, mtime, ext)
    pour tous les fichiers et dossiers sous root_dir.
    """
    root_dir = root_dir.resolve()
    root_str = str(root_dir)

    for dirpath, dirnames, filenames in os.walk(root_str):
        # Filtrer les dossiers à ignorer
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]

        # 1) Dossier lui-même
        dir_path = Path(dirpath)
        rel_dir = os.path.relpath(dir_path, root_str)
        if rel_dir == ".":
            rel_dir = ""  # racine

        name = dir_path.name
        is_dir = 1
        size = None
        try:
            mtime = int(dir_path.stat().st_mtime)
        except FileNotFoundError:
            continue
        ext = None

        relative_path = rel_dir if rel_dir else name

        yield (
            str(dir_path),
            relative_path,
            name,
            is_dir,
            size,
            mtime,
            ext,
        )

        # 2) Fichiers
        for fname in filenames:
            file_path = dir_path / fname
            try:
                stat = file_path.stat()
            except FileNotFoundError:
                continue

            rel_file = os.path.relpath(file_path, root_str)
            name = fname
            is_dir = 0
            size = stat.st_size
            mtime = int(stat.st_mtime)
            _, ext = os.path.splitext(fname)
            ext = ext or None

            yield (
                str(file_path),
                rel_file,
                name,
                is_dir,
                size,
                mtime,
                ext,
            )


def update_items(conn: sqlite3.Connection, root_dir: Path) -> tuple[int, int]:
    """
    Pour chaque entrée du filesystem :
    - si full_path existe déjà dans items -> UPDATE (size, mtime, etc.)
    - sinon -> INSERT

    Retourne (nb_inserts, nb_updates).
    """
    log(f"Scan repo: {root_dir}")
    cur = conn.cursor()

    # Charger toutes les entrées existantes pour éviter un SELECT par fichier
    existing: dict[str, int] = {}
    for row_id, full_path in cur.execute("SELECT id, full_path FROM items;"):
        existing[full_path] = row_id

    insert_sql = """
        INSERT INTO items (
            full_path, relative_path, name, is_dir, size, mtime, ext
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
    """

    update_sql = """
        UPDATE items
        SET relative_path = ?,
            name = ?,
            is_dir = ?,
            size = ?,
            mtime = ?,
            ext = ?
        WHERE id = ?;
    """

    inserts = 0
    updates = 0

    cur.execute("BEGIN")
    try:
        for (
            full_path,
            relative_path,
            name,
            is_dir,
            size,
            mtime,
            ext,
        ) in scan_repo(root_dir):

            row_id = existing.get(full_path)
            if row_id is None:
                # nouvelle entrée
                cur.execute(
                    insert_sql,
                    (
                        full_path,
                        relative_path,
                        name,
                        is_dir,
                        size,
                        mtime,
                        ext,
                    ),
                )
                inserts += 1
            else:
                # mise à jour d'une entrée existante
                cur.execute(
                    update_sql,
                    (
                        relative_path,
                        name,
                        is_dir,
                        size,
                        mtime,
                        ext,
                        row_id,
                    ),
                )
                updates += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return inserts, updates


def parse_args(argv=None) -> argparse.Namespace:
    # root = parent du dossier scripts par défaut
    default_root = Path(__file__).resolve().parents[1]
    default_db = default_root / "bot_structure.db"

    p = argparse.ArgumentParser(description="Mettre à jour bot_structure.db (table items) en scannant le dossier du bot.")
    p.add_argument(
        "--root",
        type=str,
        default=str(default_root),
        help=f"Dossier racine du bot à scanner. Défaut: {default_root}",
    )
    p.add_argument(
        "--db",
        type=str,
        default=str(default_db),
        help=f"Chemin de la base SQLite existante. Défaut: {default_db}",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    root_dir = Path(args.root).resolve()
    db_path = Path(args.db).resolve()

    if not root_dir.is_dir():
        log(f"ERREUR: root_dir n'est pas un dossier: {root_dir}")
        return 1

    log(f"ROOT_DIR = {root_dir}")
    log(f"DB_PATH  = {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        if not table_items_exists(conn):
            log("ERREUR: la table 'items' n'existe pas dans cette base. Je ne fais rien.")
            return 1

        inserts, updates = update_items(conn, root_dir)
        log(f"Terminé. {inserts} nouvelles entrées, {updates} mises à jour.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


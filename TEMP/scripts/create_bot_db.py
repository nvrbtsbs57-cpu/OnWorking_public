import os
import sqlite3
import time

# ✅ Dossier principal de ton bot
# On part du dossier où se trouve ce fichier (scripts/),
# puis on remonte d'un cran pour tomber sur le dossier racine du bot.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

# Si une variable d'environnement ROOT_DIR est définie, on la prend,
# sinon on utilise le DEFAULT_ROOT_DIR calculé ci-dessus.
ROOT_DIR = os.getenv("ROOT_DIR", DEFAULT_ROOT_DIR)

# ✅ Le .db sera créé DANS le dossier principal du bot
DB_PATH = os.path.join(ROOT_DIR, "bot_structure.db")


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            name TEXT NOT NULL,
            is_dir INTEGER NOT NULL,
            size INTEGER,
            mtime INTEGER,
            ext TEXT
        )
    """)
    # 🧹 On vide la table à chaque exécution pour repartir proprement
    cur.execute("DELETE FROM items")
    conn.commit()
    return conn


def scan_folder(root_dir, conn):
    cur = conn.cursor()
    count = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Dossiers
        for d in dirnames:
            full_path = os.path.join(dirpath, d)
            relative_path = os.path.relpath(full_path, root_dir)
            try:
                stat = os.stat(full_path)
                mtime = int(stat.st_mtime)
            except FileNotFoundError:
                mtime = None

            cur.execute("""
                INSERT INTO items (full_path, relative_path, name, is_dir, size, mtime, ext)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (full_path, relative_path, d, 1, None, mtime, None))
            count += 1

        # Fichiers
        for f in filenames:
            full_path = os.path.join(dirpath, f)
            relative_path = os.path.relpath(full_path, root_dir)
            try:
                stat = os.stat(full_path)
                size = stat.st_size
                mtime = int(stat.st_mtime)
            except FileNotFoundError:
                size = None
                mtime = None

            name, ext = os.path.splitext(f)
            ext = ext.lower() if ext else None

            cur.execute("""
                INSERT INTO items (full_path, relative_path, name, is_dir, size, mtime, ext)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (full_path, relative_path, f, 0, size, mtime, ext))
            count += 1

        if count % 500 == 0:
            conn.commit()
            print(f"{count} éléments enregistrés...")

    conn.commit()
    print(f"Terminé. {count} éléments au total.")


if __name__ == "__main__":
    print(f"ROOT_DIR utilisé : {ROOT_DIR}")
    print(f"DB_PATH utilisé  : {DB_PATH}")

    if not os.path.isdir(ROOT_DIR):
        print(f"Le dossier ROOT_DIR n'existe pas : {ROOT_DIR}")
    else:
        print(f"Initialisation de la base : {DB_PATH}")
        conn = init_db(DB_PATH)
        print(f"Scan du dossier : {ROOT_DIR}")
        scan_folder(ROOT_DIR, conn)
        conn.close()
        print(f"Base créée / mise à jour : {DB_PATH}")
import os
import sqlite3
import time

# ✅ Dossier principal de ton bot
# On part du dossier où se trouve ce fichier (scripts/),
# puis on remonte d'un cran pour tomber sur le dossier racine du bot.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

# Si une variable d'environnement ROOT_DIR est définie, on la prend,
# sinon on utilise le DEFAULT_ROOT_DIR calculé ci-dessus.
ROOT_DIR = os.getenv("ROOT_DIR", DEFAULT_ROOT_DIR)

# ✅ Le .db sera créé DANS le dossier principal du bot
DB_PATH = os.path.join(ROOT_DIR, "bot_structure.db")


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            name TEXT NOT NULL,
            is_dir INTEGER NOT NULL,
            size INTEGER,
            mtime INTEGER,
            ext TEXT
        )
    """)
    # 🧹 On vide la table à chaque exécution pour repartir proprement
    cur.execute("DELETE FROM items")
    conn.commit()
    return conn


def scan_folder(root_dir, conn):
    cur = conn.cursor()
    count = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Dossiers
        for d in dirnames:
            full_path = os.path.join(dirpath, d)
            relative_path = os.path.relpath(full_path, root_dir)
            try:
                stat = os.stat(full_path)
                mtime = int(stat.st_mtime)
            except FileNotFoundError:
                mtime = None

            cur.execute("""
                INSERT INTO items (full_path, relative_path, name, is_dir, size, mtime, ext)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (full_path, relative_path, d, 1, None, mtime, None))
            count += 1

        # Fichiers
        for f in filenames:
            full_path = os.path.join(dirpath, f)
            relative_path = os.path.relpath(full_path, root_dir)
            try:
                stat = os.stat(full_path)
                size = stat.st_size
                mtime = int(stat.st_mtime)
            except FileNotFoundError:
                size = None
                mtime = None

            name, ext = os.path.splitext(f)
            ext = ext.lower() if ext else None

            cur.execute("""
                INSERT INTO items (full_path, relative_path, name, is_dir, size, mtime, ext)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (full_path, relative_path, f, 0, size, mtime, ext))
            count += 1

        if count % 500 == 0:
            conn.commit()
            print(f"{count} éléments enregistrés...")

    conn.commit()
    print(f"Terminé. {count} éléments au total.")


if __name__ == "__main__":
    print(f"ROOT_DIR utilisé : {ROOT_DIR}")
    print(f"DB_PATH utilisé  : {DB_PATH}")

    if not os.path.isdir(ROOT_DIR):
        print(f"Le dossier ROOT_DIR n'existe pas : {ROOT_DIR}")
    else:
        print(f"Initialisation de la base : {DB_PATH}")
        conn = init_db(DB_PATH)
        print(f"Scan du dossier : {ROOT_DIR}")
        scan_folder(ROOT_DIR, conn)
        conn.close()
        print(f"Base créée / mise à jour : {DB_PATH}")


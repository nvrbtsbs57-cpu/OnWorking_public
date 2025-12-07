from pathlib import Path

# Racines MAIN et BACKUP
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_ROOT = PROJECT_ROOT
BACKUP_ROOT = Path.home() / "Documents" / "BOT_GODMODE"

MAIN_BOT = MAIN_ROOT / "bot"
BACKUP_BOT = BACKUP_ROOT / "bot"

MAIN_SCRIPTS = MAIN_ROOT / "scripts"
BACKUP_SCRIPTS = BACKUP_ROOT / "scripts"

KEY_FILES = [
    "config.json",
    "bot_structure.db",
    "data/godmode/wallets_runtime.json",
    "data/godmode/trades.jsonl",
]


def collect_py_files(root: Path):
    return {p.relative_to(root) for p in root.rglob("*.py")}


def report_diff(label: str, main_root: Path, backup_root: Path):
    print(f"=== {label} ===")
    print(f"MAIN  : {main_root}")
    print(f"BACKUP: {backup_root}")

    if not backup_root.exists():
        print(f"[WARN] Backup path does not exist: {backup_root}")
        print("-" * 60)
        return

    if not main_root.exists():
        print(f"[ERR] Main path does not exist: {main_root}")
        print("-" * 60)
        return

    main_files = collect_py_files(main_root)
    backup_files = collect_py_files(backup_root)

    missing_in_main = sorted(backup_files - main_files)
    extra_in_main = sorted(main_files - backup_files)

    print(f"Total .py in MAIN   : {len(main_files)}")
    print(f"Total .py in BACKUP : {len(backup_files)}")
    print()

    if missing_in_main:
        print(">>> MISSING in MAIN (present in BACKUP):")
        for rel in missing_in_main:
            print(f"  - {rel}")
    else:
        print(">>> OK: no files missing in MAIN vs BACKUP.")

    print()

    if extra_in_main:
        print(">>> EXTRA in MAIN (not in BACKUP):")
        for rel in extra_in_main:
            print(f"  - {rel}")
    else:
        print(">>> OK: no extra files in MAIN vs BACKUP.")

    print("-" * 60)
    print()


def check_key_files():
    print("=== Key files in MAIN project ===")
    print(f"PROJECT_ROOT = {PROJECT_ROOT}")
    for rel in KEY_FILES:
        path = PROJECT_ROOT / rel
        status = "OK  " if path.exists() else "MISS"
        print(f"[{status}] {rel}")
    print("-" * 60)
    print()


def main():
    print("=== BOT_GODMODE – compare_with_backup ===")
    print(f"MAIN   root : {MAIN_ROOT}")
    print(f"BACKUP root : {BACKUP_ROOT}")
    print()

    check_key_files()

    report_diff("bot/", MAIN_BOT, BACKUP_BOT)
    report_diff("scripts/", MAIN_SCRIPTS, BACKUP_SCRIPTS)


if __name__ == "__main__":
    main()

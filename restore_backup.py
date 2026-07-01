#!/usr/bin/env python3
"""
Restore the game binary to the state it was in before apply_patch.py ran
(i.e. undoes the crash patch, but keeps Step 1's launch fix in place, since
that's what apply_patch.py backed up from).

Usage:
    python3 restore_backup.py
    python3 restore_backup.py "/path/to/Steam/steamapps/common/Asterix & Obelix XXL 1"
"""

import subprocess
import sys
from pathlib import Path

BACKUP_SUFFIX = ".pre-crashfix-patch.bak"

DEFAULT_STEAM_DIR = (
    Path.home()
    / "Library/Application Support/Steam/steamapps/common/Asterix & Obelix XXL 1"
)


def find_app_and_binary(game_dir: Path):
    if not game_dir.is_dir():
        sys.exit(f"Game folder not found:\n  {game_dir}")
    apps = sorted(game_dir.glob("*.app"))
    if not apps:
        sys.exit(f"No .app bundle found inside:\n  {game_dir}")
    app = apps[0]
    macos_dir = app / "Contents" / "MacOS"
    candidates = sorted(f for f in macos_dir.iterdir() if not f.name.endswith(".dylib"))
    if not candidates:
        sys.exit(f"No executable found inside:\n  {macos_dir}")
    return app, candidates[0]


def main():
    game_dir = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_STEAM_DIR
    app, binary = find_app_and_binary(game_dir)
    backup = binary.with_name(binary.name + BACKUP_SUFFIX)

    if not backup.exists():
        sys.exit(f"No backup found at:\n  {backup}\nNothing to restore.")

    binary.write_bytes(backup.read_bytes())
    print(f"Restored {binary.name} from backup.")

    print("Re-signing (ad-hoc)...")
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--verbose", str(app)], check=True)
    print("Restore complete.")


if __name__ == "__main__":
    main()

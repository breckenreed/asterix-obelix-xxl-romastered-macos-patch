#!/usr/bin/env python3
"""
Asterix & Obelix XXL: Romastered (macOS) -- "crash after Chapter 1" fix.

Root cause: the crashing function unconditionally decrements a reference
count at [object + 0x250]. When entering ANY combat world (Normandy,
Helvetia, ...) that object pointer is null, so the decrement dereferences
address 0x250 and the game dies with EXC_BAD_ACCESS / SIGSEGV.

This patches the function to check the pointer first and skip the whole
body (jumping straight to the function's own epilogue) when it's null,
instead of crashing. Everything else about the function is unchanged.

IMPORTANT: this only works AFTER the separate launch-config fix described
in README.md ("Step 1"). Run that first -- this script checks for it and
refuses to run otherwise.

Usage:
    python3 apply_patch.py
    python3 apply_patch.py "/path/to/Steam/steamapps/common/Asterix & Obelix XXL 1"
"""

import subprocess
import sys
from pathlib import Path

# --- patch locations, as file offsets (virtual_address - image_base 0x100000000) ---
# ENTRY_OFFSET: start of "mov rbx,rsi; mov r14,rdi" inside the crashing function.
#   Replaced with a 5-byte jmp into CAVE_A (+1 NOP filler byte).
# CAVE_A_OFFSET / CAVE_B_OFFSET: two 16-byte runs of function-alignment NOP
#   padding (dead space between unrelated functions, each immediately preceded
#   by a `ud2` trap so nothing ever falls through into them), repurposed as a
#   two-part code cave for the null-check detour.
ENTRY_OFFSET = 0xFA74E1
CAVE_A_OFFSET = 0x11291DB
CAVE_B_OFFSET = 0x1123E89

ORIGINAL = {
    ENTRY_OFFSET: bytes.fromhex("4889f34989fe"),
    CAVE_A_OFFSET: b"\x90" * 16,
    CAVE_B_OFFSET: b"\x90" * 16,
}

# entry:  jmp CaveA ; nop
# CaveA:  test esi,esi ; jz <function epilogue>  ; jmp CaveB ; nop*3
# CaveB:  mov rbx,rsi ; mov r14,rdi (replayed originals) ; jmp <back to original code> ; nop*5
PATCHED = {
    ENTRY_OFFSET: bytes.fromhex("e9f51c180090"),
    CAVE_A_OFFSET: bytes.fromhex("85f60f8487e3e7ffe9a1acffff909090"),
    CAVE_B_OFFSET: bytes.fromhex("4889f34989fee95336e8ff9090909090"),
}

BACKUP_SUFFIX = ".pre-crashfix-patch.bak"

DEFAULT_STEAM_DIR = (
    Path.home()
    / "Library/Application Support/Steam/steamapps/common/Asterix & Obelix XXL 1"
)


def find_app_and_binary(game_dir: Path):
    if not game_dir.is_dir():
        sys.exit(
            f"Game folder not found:\n  {game_dir}\n\n"
            "If your Steam library isn't in the default location, pass the "
            "correct folder as an argument, e.g.:\n"
            f'  python3 {Path(sys.argv[0]).name} "/path/to/Asterix & Obelix XXL 1"'
        )
    apps = sorted(game_dir.glob("*.app"))
    if not apps:
        sys.exit(f"No .app bundle found inside:\n  {game_dir}")
    app = apps[0]
    macos_dir = app / "Contents" / "MacOS"
    candidates = sorted(f for f in macos_dir.iterdir() if not f.name.endswith(".dylib"))
    if not candidates:
        sys.exit(f"No executable found inside:\n  {macos_dir}")
    return app, candidates[0]


def launch_fix_applied(app: Path, binary: Path) -> bool:
    """Checks whether Step 1 (Info.plist CFBundleExecutable fix) has been done."""
    plist = app / "Contents" / "Info.plist"
    try:
        result = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleExecutable", str(plist)],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return result.stdout.strip() == binary.name


def region_matches(data: bytes, table: dict) -> bool:
    return all(data[off:off + len(seq)] == seq for off, seq in table.items())


def main():
    game_dir = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_STEAM_DIR
    app, binary = find_app_and_binary(game_dir)
    print(f"Found game binary:\n  {binary}\n")

    if not launch_fix_applied(app, binary):
        sys.exit(
            "The launch-config fix (Step 1 in README.md) doesn't look like it's "
            "been applied yet -- CFBundleExecutable in Info.plist doesn't match "
            "the actual binary name.\n\n"
            "Do Step 1 first, then re-run this script."
        )

    data = binary.read_bytes()

    if region_matches(data, PATCHED):
        print("Patch already applied -- nothing to do.")
        return

    if not region_matches(data, ORIGINAL):
        sys.exit(
            "This binary's bytes at the patch locations don't match what this "
            "patch expects (neither the original nor the already-patched form).\n\n"
            "This most likely means Steam shipped a different build/version than "
            "the one this patch was made for. Refusing to modify the file -- "
            "nothing has been changed.\n\n"
            "Please open an issue on the repo with your game's build info "
            "(Steam -> right-click game -> Properties -> Installed Files)."
        )

    backup = binary.with_name(binary.name + BACKUP_SUFFIX)
    if not backup.exists():
        backup.write_bytes(data)
        print(f"Backed up original binary to:\n  {backup.name}\n")
    else:
        print(f"Backup already exists ({backup.name}), leaving it as-is.\n")

    patched = bytearray(data)
    for off, seq in PATCHED.items():
        patched[off:off + len(seq)] = seq
    binary.write_bytes(bytes(patched))
    print("Patch written.")

    print("Re-signing (ad-hoc)...")
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--verbose", str(app)], check=True)

    print()
    print("Done. Launch the game from Steam as usual -- Normandy, Helvetia, and")
    print("other combat worlds should now load instead of crashing.")
    print()
    print("If Steam's 'Verify integrity of game files' is ever run, it will undo")
    print("this patch (and Step 1). Just re-run Step 1 and this script again.")


if __name__ == "__main__":
    main()

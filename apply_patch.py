#!/usr/bin/env python3
"""
Asterix & Obelix XXL: Romastered (macOS) -- "crash after Chapter 1" fixes.

Applies two independent binary patches to the shipped x86_64 game binary, both
of which stop a hard crash when entering the combat worlds (Normandy, Helvetia,
Egypt). Each patch is a self-contained detour into unused NOP alignment padding
("code caves"); no code is appended and nothing in the file moves.

  1. Null-deref refcount crash (FUN_100fa74d6). The function decrements a
     refcount at [object + 0x250]; that object pointer is null on these worlds,
     so it faults at address 0x250. The patch null-checks first and skips to the
     function epilogue when null.

  2. Texture config-index dispatch crash (FUN_1003dec5a, file offset 0x3df57d).
     After uploading a texture, the function does an indirect jump through an
     8-entry table indexed by a runtime "sampler config" field, texture[0xc0].
     That field is constructor-initialised to -1 and is never set for some
     textures (the first to fault in Normandy is an ordinary 16x16), so the
     index is -1 and the jump lands on a wild address. The patch range-checks
     the index and, when it is outside [0, 7], skips the whole sampler-config
     block to the function epilogue (leaving GL default filtering, which renders
     fine). See TERRAIN_NOTES.md for how this was isolated.

Neither patch affects the (separate, unsolved) invisible slide/pre-fight terrain
in those worlds; see TERRAIN_NOTES.md.

IMPORTANT: this only works AFTER the separate launch-config fix described in
README.md ("Step 1"). Run that first -- this script checks for it and refuses
to run otherwise.

Usage:
    python3 apply_patch.py
    python3 apply_patch.py "/path/to/Steam/steamapps/common/Asterix & Obelix XXL 1"
"""

import subprocess
import sys
from pathlib import Path

# Each patch is a set of regions: file_offset -> (original_hex, patched_hex).
# File offset = virtual_address - image_base (0x100000000). The cave locations
# are 16-byte runs of inter-function NOP alignment padding (each preceded by a
# `ud2` trap, so nothing ever falls through into them). The two patches use
# different caves and different hook sites, so they are fully independent.
PATCHES = [
    {
        "name": "combat-world null-deref crash",
        "regions": {
            0xFA74E1: ("4889f34989fe", "e9f51c180090"),
            0x11291DB: ("90" * 16, "85f60f8487e3e7ffe9a1acffff909090"),
            0x1123E89: ("90" * 16, "4889f34989fee95336e8ff9090909090"),
        },
    },
    {
        # entry @0x3df56a: jmp CaveA ; nop ; nop  (replaces `mov eax,[r15+0xc0]`)
        # CaveA: mov eax,[r15+0xc0] ; cmp eax,7 ; jmp CaveB
        # CaveB: ja <epilogue 0x3decf7> ; jmp <resume 0x3df571>
        "name": "texture config-index (0xc0) dispatch crash",
        "regions": {
            0x3DF56A: ("418b87c0000000", "e96607d4009090"),
            0x111FCD5: ("90" * 16, "418b87c000000083f807e959993dff90"),
            0x4F963D: ("90" * 16, "0f87b456eeffe9295feeff9090909090"),
        },
    },
]

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


def patch_state(data: bytes, patch: dict) -> str:
    """Return 'applied', 'original', or 'unknown' for one patch's regions."""
    applied = all(
        data[off:off + len(bytes.fromhex(p))] == bytes.fromhex(p)
        for off, (_o, p) in patch["regions"].items()
    )
    if applied:
        return "applied"
    original = all(
        data[off:off + len(bytes.fromhex(o))] == bytes.fromhex(o)
        for off, (o, _p) in patch["regions"].items()
    )
    return "original" if original else "unknown"


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

    data = bytearray(binary.read_bytes())

    states = {p["name"]: patch_state(data, p) for p in PATCHES}
    for name, state in states.items():
        print(f"  {name}: {state}")
    print()

    if any(s == "unknown" for s in states.values()):
        sys.exit(
            "At least one patch location doesn't match either the original or "
            "the already-patched bytes this script expects.\n\n"
            "This most likely means Steam shipped a different build/version than "
            "the one these patches were made for. Refusing to modify the file -- "
            "nothing has been changed.\n\n"
            "Please open an issue on the repo with your game's build info "
            "(Steam -> right-click game -> Properties -> Installed Files)."
        )

    to_apply = [p for p in PATCHES if states[p["name"]] == "original"]
    if not to_apply:
        print("All patches already applied -- nothing to do.")
        return

    backup = binary.with_name(binary.name + BACKUP_SUFFIX)
    if not backup.exists():
        backup.write_bytes(bytes(data))
        print(f"Backed up original binary to:\n  {backup.name}\n")
    else:
        print(f"Backup already exists ({backup.name}), leaving it as-is.\n")

    for patch in to_apply:
        for off, (_o, p) in patch["regions"].items():
            seq = bytes.fromhex(p)
            data[off:off + len(seq)] = seq
        print(f"Applied: {patch['name']}")
    binary.write_bytes(bytes(data))

    print("\nRe-signing (ad-hoc)...")
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--verbose", str(app)], check=True)

    print()
    print("Done. Launch the game from Steam as usual -- Normandy, Helvetia, and")
    print("the other combat worlds should now load instead of crashing.")
    print()
    print("If Steam's 'Verify integrity of game files' is ever run, it will undo")
    print("this patch (and Step 1). Just re-run Step 1 and this script again.")


if __name__ == "__main__":
    main()

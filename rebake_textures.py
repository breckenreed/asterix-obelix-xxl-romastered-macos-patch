#!/usr/bin/env python3
"""
Asterix & Obelix XXL: Romastered (macOS) -- terrain texture rebaker.

    !!! EXPERIMENTAL / INCOMPLETE -- DOES NOT PRODUCE WORKING FILES YET !!!
    This script correctly patches the header and promotes mip-1, but it does
    NOT reconstruct the cooked-texture footer. The result is ~20 bytes too long
    for a downscaled image, which misaligns the runtime `texture + 0xc0` field
    and CRASHES the game during load. See TERRAIN_NOTES.md for the full story.
    It is kept only as a reference for whoever finishes the footer handling.
    It always backs up originals (.orig4096) before writing.

The Mac build cannot load textures with a 4096 dimension (max working size is
2048x1024). The only such textures in the whole game are the terrain "atlas"
maps used by Normandy, Helvetia and Egypt -- which is exactly why those three
worlds render with missing (flat) terrain even after the crash fix.

This tool halves every oversized (>2048) cooked atlas texture to 2048x1024,
which is a proven-working size. It does NOT re-encode: a 4096x2048 texture
already stores its 2048x1024 image as mip level 1, so we drop the oversized
base level, promote mip-1 to be the new base, and rewrite the 4 header fields
that record the dimensions/size. The DXT bytes themselves are untouched, so
there is no quality loss beyond the (unavoidable) drop to half resolution.

Cooked ".Texture.dxt" layout (little-endian), verified on this build:
    0x00..0xe0   header/serialization wrapper (224 bytes)
      0xc8  uint32  width
      0xcc  uint32  height
      0xd4  uint32  format   (4 = DXT1 / 8 bytes per 4x4 block,
                              5 = DXT5 / 16 bytes per 4x4 block)
      0xd8  uint32  base-level (mip 0) size in bytes
      0xdc  uint32  base-level size (duplicate)
    0xe0..       mip chain, level 0 first, each level 1/4 the bytes
    tail         ~250-byte serialization trailer (dimension-independent)

Usage:
    python3 rebake_textures.py --self-test                 # validate logic, writes nothing
    python3 rebake_textures.py "<Atlas world folder>"      # rebake one world (backs up first)
    python3 rebake_textures.py --all                       # rebake all 3 atlas worlds in the default install
"""

import struct
import sys
import shutil
from pathlib import Path

PIXEL_START = 0xE0
OFF_W, OFF_H, OFF_FMT, OFF_SZ0, OFF_SZ1 = 0xC8, 0xCC, 0xD4, 0xD8, 0xDC
BLOCK_BYTES = {4: 8, 5: 16}          # fmt -> bytes per 4x4 block  (DXT1 / DXT5)
MAX_OK = 2048                        # largest dimension the Mac build can load
BACKUP_SUFFIX = ".orig4096"

DEFAULT_ATLAS_ROOT = (
    Path.home() / "Library/Application Support/Steam/steamapps/common/"
    "Asterix & Obelix XXL 1/Asterix & Obelix XXL _ Romastered.app/Contents/"
    "Resources/XXL1Resources/_Cooking/Atlas"
)
ATLAS_WORLDS = ["2_normandie", "4_helvetie", "5_egypte"]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def level_bytes(w, h, blk):
    bw = max(1, (w + 3) // 4)
    bh = max(1, (h + 3) // 4)
    return bw * bh * blk


def mipchain_bytes(w, h, blk):
    total = 0
    while True:
        total += level_bytes(w, h, blk)
        if w == 1 and h == 1:
            break
        w = max(1, w // 2)
        h = max(1, h // 2)
    return total


def analyze(b):
    """Return (w, h, fmt, blk, base_size, trailer_size) or None if not a recognized/oversized atlas texture."""
    if len(b) < PIXEL_START + 16:
        return None
    w, h, fmt = u32(b, OFF_W), u32(b, OFF_H), u32(b, OFF_FMT)
    if fmt not in BLOCK_BYTES:
        return None
    if w not in (32, 64, 128, 256, 512, 1024, 2048, 4096, 8192):
        return None
    if h not in (32, 64, 128, 256, 512, 1024, 2048, 4096, 8192):
        return None
    blk = BLOCK_BYTES[fmt]
    base = level_bytes(w, h, blk)
    if u32(b, OFF_SZ0) != base:                 # field-offset sanity: 0xd8 must equal computed base
        return None
    trailer = len(b) - PIXEL_START - mipchain_bytes(w, h, blk)
    if not (0 <= trailer < 4096):               # layout sanity: small, non-negative trailer
        return None
    return w, h, fmt, blk, base, trailer


def rebake_bytes(b):
    """Return new file bytes halved to <=2048, or None if this file shouldn't/can't be processed."""
    info = analyze(b)
    if info is None:
        return None
    w, h, fmt, blk, base, trailer = info
    if w <= MAX_OK and h <= MAX_OK:
        return None                              # already fine, skip
    nw, nh = w // 2, h // 2
    new_base = level_bytes(nw, nh, blk)
    mip1_start = PIXEL_START + base
    new = bytearray(b[:PIXEL_START])             # keep header
    struct.pack_into("<I", new, OFF_W, nw)
    struct.pack_into("<I", new, OFF_H, nh)
    struct.pack_into("<I", new, OFF_SZ0, new_base)
    struct.pack_into("<I", new, OFF_SZ1, new_base)
    new += b[mip1_start:]                         # promote mip-1 onward (mips + trailer)
    out = bytes(new)
    # verify the result re-parses cleanly and has the exact expected size
    chk = analyze(out)
    assert chk is not None, "rebaked file failed re-parse"
    assert (chk[0], chk[1]) == (nw, nh), "rebaked dims wrong"
    expect = PIXEL_START + mipchain_bytes(nw, nh, blk) + trailer
    assert len(out) == expect, f"rebaked size {len(out)} != expected {expect}"
    return out


def self_test():
    root = DEFAULT_ATLAS_ROOT
    samples = [
        root / "2_normandie" / "0_u_normal.png.Texture.dxt",   # DXT1
        root / "2_normandie" / "0_u_albedo.png.Texture.dxt",   # DXT5
        root / "2_normandie" / "0_u_albedoTint~W.png.Texture.dxt",  # tiny placeholder -> should skip
    ]
    for p in samples:
        if not p.exists():
            print(f"  (missing sample {p.name})")
            continue
        b = p.read_bytes()
        info = analyze(b)
        out = rebake_bytes(b)
        if out is None:
            print(f"  {p.name:34s}: SKIP (info={info})")
        else:
            oi = analyze(out)
            print(f"  {p.name:34s}: {info[0]}x{info[1]} fmt{info[2]} {len(b):>9} B  ->  "
                  f"{oi[0]}x{oi[1]} {len(out):>9} B   (trailer {info[5]} preserved: {info[5]==oi[5]})")
    print("self-test OK (no files written)")


def rebake_folder(folder):
    folder = Path(folder)
    files = sorted(folder.glob("*.Texture.dxt"))
    if not files:
        sys.exit(f"No .Texture.dxt files in {folder}")
    done = skipped = 0
    for p in files:
        b = p.read_bytes()
        out = rebake_bytes(b)
        if out is None:
            skipped += 1
            continue
        backup = p.with_name(p.name + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_bytes(b)
        p.write_bytes(out)
        done += 1
        print(f"  rebaked {p.name}  ({len(b)} -> {len(out)} B)")
    print(f"{folder.name}: {done} rebaked, {skipped} left as-is")


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--self-test":
        self_test()
    elif args[0] == "--all":
        for w in ATLAS_WORLDS:
            rebake_folder(DEFAULT_ATLAS_ROOT / w)
    else:
        rebake_folder(args[0])


if __name__ == "__main__":
    main()

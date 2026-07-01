# Asterix & Obelix XXL: Romastered - macOS fix

Two problems with the Steam macOS build of **Asterix & Obelix XXL: Romastered**,
and fixes for both:

1. **The game won't launch at all** on a fresh install (Apple Silicon and
   Intel). This is a broken app-bundle config, unrelated to the crash below.
2. **The game crashes when entering any combat world** - Normandy, Helvetia,
   and onward - right after you finish the tutorial/hub area. This is a real
   bug in the shipped binary. The publisher has not patched it.

This repo fixes #2. Fixing #1 is a required first step, done manually, before
the patch here will do anything useful (a game that can't launch obviously
can't be tested for a crash further in).

**This does not distribute any game files.** You need to own the game on
Steam already. The script here patches *your own local copy* in place.

## Requirements

- macOS
- Asterix & Obelix XXL: Romastered, purchased and installed via Steam
- Terminal (Applications → Utilities → Terminal) - used for both steps below
- Python 3 (already included on macOS)

## Step 1 - Fix the launch bug (do this first, manually)

This step is **not** part of the patch script below - it has to be done by
hand, once, before the game will even open.

**What's wrong:** the app bundle's `Info.plist` points `CFBundleExecutable`
at a filename that doesn't match the actual binary on disk, so macOS can't
find anything to run. These two commands fix that mismatch and re-sign the
app (editing `Info.plist` invalidates the original signature, which macOS
would otherwise refuse to run).

Open Terminal and run:

**Command 1 - fix the app's launch config**
```bash
G="$HOME/Library/Application Support/Steam/steamapps/common/Asterix & Obelix XXL 1"; A=$(find "$G" -maxdepth 1 -name '*.app' | head -1); B=$(ls "$A/Contents/MacOS" | grep -v '\.dylib$' | head -1); cp -n "$A/Contents/Info.plist" "$A/Contents/Info.plist.bak"; /usr/libexec/PlistBuddy -c "Set :CFBundleExecutable $B" "$A/Contents/Info.plist" && echo "OK: CFBundleExecutable -> $B"
```

**Command 2 - clear quarantine + re-sign the app**
```bash
G="$HOME/Library/Application Support/Steam/steamapps/common/Asterix & Obelix XXL 1"; A=$(find "$G" -maxdepth 1 -name '*.app' | head -1); xattr -cr "$A"; codesign --force --deep --sign - "$A" && echo "OK: re-signed"
```

Launch the game from Steam once to confirm it opens to the menu. If it does,
move on to Step 2. (If your install already opened fine before this step,
that's fine too - some Steam downloads already have this fixed. Step 2's
script checks for this and will tell you if you're good to skip ahead.)

## Step 2 - Apply the crash fix

Clone this repo (or [download it as a ZIP](../../archive/refs/heads/main.zip)
and unzip it), then from Terminal:

```bash
cd /path/to/asterix-obelix-xxl-romastered-macos-patch
python3 apply_patch.py
```

That's it. The script will:
1. Auto-detect your game install (default Steam location)
2. Confirm Step 1 was done
3. Verify the binary matches what this patch expects, **before changing
   anything**
4. Back up the original binary
5. Apply the patch
6. Re-sign the app

If your Steam library is somewhere non-default, pass the game folder as an
argument:
```bash
python3 apply_patch.py "/Volumes/MyDrive/SteamLibrary/steamapps/common/Asterix & Obelix XXL 1"
```

Then just launch the game from Steam normally.

### Verifying it worked

Play from the start of the game into the first combat world (Normandy). It
should load instead of crashing to desktop. Helvetia and later worlds should
work the same way.

### Undoing the patch

```bash
python3 restore_backup.py
```
Restores the binary this script backed up before patching (leaves Step 1's
launch fix in place). Same optional path argument as above if needed.

### If Steam "verifies" your files

Steam's **Verify integrity of game files** will restore the original,
broken `Info.plist` and the original, crashing binary - undoing both Step 1
and this patch. If that happens, just redo Step 1 and re-run
`apply_patch.py`.

### If the script refuses to patch your binary

`apply_patch.py` checks the exact bytes at the patch location before writing
anything - it will **not** touch the file if they don't match what this
patch was built for, and prints a clear error instead. If you hit that,
please [open an issue](../../issues) with your game's build info (Steam →
right-click the game → Properties → Installed Files).

---

## Technical details

For anyone curious what's actually broken and what this changes.

### The bug

The crash is `EXC_BAD_ACCESS` (SIGSEGV) reading address `0x250` - a
near-null pointer dereference. The crashing function is a reference-count
decrement (a `Release()`-style pattern):

```c
void FUN_100fa74d6(long param_1, long param_2)
{
    int *piVar1 = (int *)(param_2 + 0x250);
    *piVar1 = *piVar1 - 1;          // <-- crashes here
    if (*piVar1 == 0) {
        FUN_100c549a4(param_2 + 0x18, 0);
    }
    ...                             // rest of the function also uses param_2
}
```

`param_2` is null every time this fires - reliably, on every combat-world
load, never on the hub/village. In assembly, the crash instruction is:

```
100fa74e7: ff 8e 50 02 00 00    dec  dword ptr [rsi + 0x250]
```

`rsi` holds `param_2`. If `rsi` is `0`, `[rsi + 0x250]` is address `0x250` -
exactly the fault address every crash report shows. Something upstream fails
to hand this function a valid resource object on this platform, and there's
no null check before the decrement.

### The fix

Rather than chase why the object ends up null (a much bigger unknown without
engine source), the patch makes this one function tolerate it: check the
pointer, and if it's null, skip straight to the function's own
stack-cleanup/return instead of touching it. Everything else is unchanged.

Concretely, at the function's entry (right after its prologue, before the
decrement), the two instructions that save the parameters into callee-saved
registers are replaced with a `jmp` into a small two-part patch inserted
into unused alignment padding elsewhere in the binary:

```
entry:  jmp CaveA ; nop

CaveA:  test esi, esi
        jz   <function's epilogue>      ; null -> skip everything, return
        jmp  CaveB

CaveB:  mov  rbx, rsi                   ; replay the two overwritten
        mov  r14, rdi                   ; instructions
        jmp  <back into original code>  ; non-null -> resume normally
```

The two 16-byte cave locations are existing NOP alignment padding between
unrelated functions (each preceded by a `ud2` trap, confirming nothing ever
falls through into them) - no new code is appended to the binary, and
nothing else in the file moves.

### Why two caves instead of one

The straightforward version of this patch needs ~19 contiguous bytes; the
largest single padding gap found in this binary is 16. Rather than grow the
file (which means rewriting Mach-O load commands and is a lot more invasive
for a 3-byte savings), the logic is split across two 16-byte gaps chained by
a `jmp`.

### Compatibility

Built and tested on Apple Silicon (M-series). The binary is x86_64 either
way (runs under Rosetta 2 on Apple Silicon, natively on Intel Macs), and the
bug itself is a straightforward, unconditional code path - not something
tied to Rosetta specifically - so this should apply the same way on Intel
Macs, but that hasn't been separately verified. If you test on Intel and hit
anything different, please open an issue.

This patch targets one specific Steam build of the game. `apply_patch.py`
checks the exact original bytes before writing anything, so a different
build will be safely rejected rather than silently corrupted - see
"If the script refuses to patch your binary" above.

## Disclaimer

No warranty. This modifies a copy of a game binary you already legitimately
own, purely for personal bug-fixing - no game files are redistributed by
this repo, and the script always backs up before writing. Still, back up
your save files separately before experimenting.

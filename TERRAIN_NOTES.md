# Terrain textures missing in Normandy / Helvetia / Egypt (macOS)

Status: **root cause identified and confirmed in code; a clean fix is not yet
available.** This document records the investigation so it can be finished by
anyone with the game's cooking tools or more patience for the cooked-texture
format. The main crash fix in this repo is unaffected and the game is fully
playable start to finish.

## Symptom

After applying the crash fix, three of the six worlds load but render with
**flat, untextured terrain** (you see the sky/void colour where the ground
should be; characters, crates, enemies, HUD etc. are all textured normally):

- World 2, **Normandy**
- World 4, **Helvetia**
- World 5, **Egypt**

Worlds 1 (Gaul), 3 (Greece) and 6 (Rome) are completely fine.

## Root cause (confirmed)

Those three worlds are the only ones that use the large **PBR terrain atlas**
system. Their atlas textures live in:

```
.../XXL1Resources/_Cooking/Atlas/2_normandie/
.../XXL1Resources/_Cooking/Atlas/4_helvetie/
.../XXL1Resources/_Cooking/Atlas/5_egypte/
```

Each world has ~13 sets of `N_u_albedo / N_u_gloss / N_u_normal / N_u_specular`
maps. Scanning **all 8,521** cooked textures in the game:

```
largest-axis histogram:  32:202  64:841  128:880  256:2072  512:2504  1024:911  2048:437  4096:80
```

**All 80 of the 4096-dimension textures are these atlas maps; zero 4096
textures exist anywhere else.** The largest texture that loads fine anywhere in
the game is 2048x1024.

The reason is a hard limit in the texture-upload path. In the upload function
(`FUN_1003dec5a` in the shipped x86_64 binary):

```c
width  = texture[0xd4];
height = texture[0xd8];
if (glctx[0x444] < width || glctx[0x444] < height)
    goto skip_upload;          // texture is left null
```

and `glctx[0x444]` is filled once at context init (`FUN_1003dd48e`):

```
LEA  RSI, [ctx + 0x444]
MOV  EDI, 0xd33               ; GL_MAX_TEXTURE_SIZE
CALL glGetIntegerv            ; ctx[0x444] = glGetIntegerv(GL_MAX_TEXTURE_SIZE)
```

On Apple Silicon the game runs under **Rosetta 2**, and its **legacy OpenGL**
context (translated to Metal) reports `GL_MAX_TEXTURE_SIZE < 4096`. So every
4096-wide atlas texture fails the check and is **skipped**, leaving the atlas
resource null.

### The crash and the missing terrain are the same bug

Before the crash fix, entering these worlds crashed because the engine later
tried to release/refcount that null atlas resource
(`dec dword ptr [rsi + 0x250]` with `rsi == 0`, faulting at address `0x250`).
The crash fix guards that null dereference, so the world now loads - but the
atlas texture is still null, hence the flat terrain. One underlying cause, two
symptoms.

## What was ruled out

- **Format**: the atlas maps use ordinary DXT1 / DXT5, identical to textures
  that load fine. Not exotic.
- **Total VRAM / memory**: the game uses only a few hundred MB; the system has
  plenty free. Not a memory ceiling.
- **A spoofable software check**: patching the binary to bypass the
  `GL_MAX_TEXTURE_SIZE` comparison was tested. The game then *attempts* the
  4096 upload and `glCompressedTexImage2D` fails at the driver - terrain stays
  invisible, no improvement. **The 4096 limit is a real driver/hardware
  constraint of the legacy-GL-under-Rosetta context, not a fake value.**

Conclusion: the only viable fix is to make the atlas textures **<= 2048** in
every dimension.

## The fix that should work, and why it is blocked

Downscaling each 4096x2048 atlas texture to 2048x1024 keeps UVs correct
(they are normalised) and passes the size check. Because a 4096x2048 texture
already contains its 2048x1024 image as mip level 1, you can rebuild without a
DXT encoder by dropping the oversized base level and promoting mip-1.

The cooked `.Texture.dxt` layout (little-endian) is:

```
0x00 .. 0xe0   header / serialization wrapper (224 bytes)
   0xc8  u32  width
   0xcc  u32  height
   0xd4  u32  format     (4 = DXT1 / 8 bytes per 4x4 block, 5 = DXT5 / 16 bytes)
   0xd8  u32  mip-0 size in bytes
   0xdc  u32  mip-0 size (duplicate)
0xe0 ..        mip chain, level 0 first, each level 1/4 the bytes
tail           a variable-length footer (see below)  <-- the blocker
```

Patching the header (0xc8/0xcc/0xd8/0xdc) and slicing off the base level is
easy. The **footer is the problem**. It is not a fixed trailer:

- Its size depends on the mip count (a 4096 texture's footer is ~20 bytes
  longer than a 2048 texture's).
- It is **content-specific** (only ~130 of 259 bytes match between two
  different textures of the same size), containing what look like the smallest
  mip levels plus per-texture metadata, delimited by `aabb2222 / aabb1111`
  serialization markers, with a tail of floats/flags
  (e.g. `0x00007ac4 / 0x00007a44` = -1000.0 / 1000.0).
- It is read back into the runtime texture struct; one of its fields ends up at
  `texture + 0xc0` and is used as a **switch/jump-table index** during upload.
  If the footer is misaligned by even a few bytes, that index is garbage and
  the upload jumps to a wild address and crashes.

A naive rebake (correct header + promoted mip chain + the *original* footer
carried over verbatim) makes the file 20 bytes too long, misaligns
`texture + 0xc0`, and crashes on load. Reconstructing a correct footer for the
downscaled image, byte-for-byte, is undocumented-format reverse engineering,
and there is no ground-truth 2048 atlas to validate against.

## For anyone continuing this

Most reliable paths, best first:

1. **Re-cook the atlases with the engine's own tools.** These are OSome Studio
   / "oC" engine cooked assets (RenderWare-derived). If the original cooking
   pipeline or an editor build is available, re-export the three worlds' atlas
   textures at 2048x1024 (or 2048x2048 for the few square ones). This produces
   footers the engine wrote itself, guaranteed valid.
2. **Fully reverse the footer format** and teach `rebake_textures.py` to
   regenerate it. The `aabb2222 / aabb1111` block structure is parsed in the
   git history of this branch; what remains is understanding the initial
   variable chunk and which mip levels/metadata it encodes.
3. **Clone-a-native-footer heuristic** (untested): keep the correct 2048 header
   + promoted mip chain, but replace the footer with a *valid* footer copied
   from a native 2048x1024 texture of the same format. The crash-causing
   `texture + 0xc0` field would then be valid; the only "wrong" bytes are the
   near-invisible 4x4-and-smaller mip levels. Might load with a faint colour
   tint at extreme distance; might trip on dimension-specific metadata in the
   tail. Not yet tried.

`rebake_textures.py` (in this repo) implements the header patch + mip promotion
and is a good starting point, but **it does not handle the footer and therefore
currently produces files that crash the game.** It is included only as a
reference for continuation, and it always backs up originals before writing.

## Reproduction / analysis environment used

- Ghidra 12.1.2 for static analysis of the x86_64 binary.
- `lldb` attach for dynamic confirmation. Note: on a Rosetta process, `lldb`
  can breakpoint the game's own code but **cannot** insert software breakpoints
  into the shared-cache system libraries (e.g. `libGL.dylib`); hardware
  breakpoints (`breakpoint set -H`) insert but resuming can still destabilise
  the process. The confirmations above are from breakpoints in the game binary
  plus static analysis, not GL-layer tracing.

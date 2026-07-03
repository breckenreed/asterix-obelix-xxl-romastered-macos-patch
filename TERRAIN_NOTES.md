# Terrain textures missing in Normandy / Helvetia / Egypt (macOS)

> **Note:** the two sections immediately below (the `GL_MAX_TEXTURE_SIZE` size
> theory and the downscale/re-bake plan) were the first investigation and are
> now known to be **wrong**. Read them for history, but see
> "Final update: the size theory is wrong, and what is actually left" at the
> bottom for the corrected conclusion. The crash fixes in this repo are
> unaffected and the game is fully playable start to finish.

Status: the load-time crashes are fixed (see `apply_patch.py`); the only
remaining cosmetic issue is invisible slide / pre-fight terrain in these three
worlds, which is a Mac-specific mesh-rendering problem (details at the bottom).

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

## Update (live debugging): the load-time crash is a distinct bug from the 4096 limit

The size-limit analysis above explains the **flat terrain** (oversized 4096
atlas maps are skipped by the `GL_MAX_TEXTURE_SIZE` guard). Later live debugging
of the texture-upload path isolated the **load-time crash** as a *separate*
fault with a different mechanism, and corrects part of the footer theory above.

### Method (no debugger attach)

Attaching `lldb` destabilises this Rosetta process and, as observed here, makes
the running game unable to see its own save files while a debugger is attached
(the save files on disk are untouched; only the attached process fails to read
them). To avoid that, the crashing texture was inspected **without** attaching a
debugger at all:

- A short code-cave detour is written into the upload function immediately
  before the faulting instruction. It copies one chosen field of the live
  texture object into an otherwise-unused register (`r14`), then falls through
  to the original crash.
- The value is read back out of the **OS-generated crash report** (`.ips`),
  whose thread state records every register at the moment of the fault.

This leaks one 64-bit field per crash with no runtime instrumentation and no
effect on saves. It was used to read the texture's dimensions, format enum,
config index and pixel-data pointer across successive crashes.

### What the crash actually is

Faulting instruction, at file offset `0x3df57d` inside `FUN_1003dec5a`:

```
mov    eax, [r15 + 0xc0]        ; eax = texture[0xc0]  (config index)
lea    rcx, [rip + ...]         ; rcx = jump-table base (DAT_1003df6a0, 8 entries)
movsxd rax, [rcx + 4*rax]       ; <-- faults
add    rax, rcx
jmp    rax
```

It is a relative jump-table dispatch indexed by `texture[0xc0]`. Register values
leaked at the fault:

| field                        | value        | meaning                                            |
|------------------------------|--------------|----------------------------------------------------|
| `texture[0xc0]` (index)      | `0xffffffff` | -1, never initialised                              |
| `texture[0xbc]` (upload fmt) | `0`          | valid: the uncompressed-RGBA path (`glTexImage2D` runs) |
| `texture[0xa8]` (pixel data) | non-null     | the pixel data *is* loaded                          |
| `texture` width x height     | `16 x 16`    | a small texture, not a 4096/2048 atlas map         |

`texture[0xc0]` is a **runtime** field: the constructor (`FUN_100c06200`) sets it
to `-1`, and the load path never updates it for these textures. With the index at
`-1`, `4*rax` addresses far outside the 8-entry table and the indirect jump reads
a wild address.

### Corrections to the record

- **The crash is not caused by the 4096 size limit.** The faulting texture is
  16x16 and reaches the dispatch through the *normal* upload path (it passes the
  `GL_MAX_TEXTURE_SIZE` guard). The size limit still explains the flat terrain,
  but it is a separate failure from this crash.
- **`texture[0xc0]` is not read from the cooked-file footer.** It is a
  constructor-initialised runtime field. Byte-perfect downscaled atlas files
  (correct header + promoted mip chain + a footer copied verbatim from a real
  native 2048 texture of the same size/format, i.e. structurally identical to a
  file the engine loads fine) still hit this crash. So "footer misalignment
  corrupts `texture + 0xc0`" was not the mechanism; the field is simply never
  set for these textures.
- The affected textures are not only the atlas maps: the first one to fault is an
  ordinary 16x16 texture with a valid format and loaded data.

### Where a fix should go

Only `texture[0xc0]` is wrong (format and data are both valid and present), so
two targeted options exist, neither needing the cooking tools:

1. **Guard the dispatch.** Skip the indexed jump when `texture[0xc0]` is outside
   `[0, 7]`. This is a small binary patch in `FUN_1003dec5a`.
2. **Initialise the index.** Set `texture[0xc0]` from `texture[0xbc]` / the value
   the working textures carry, so the correct post-upload configuration routine
   runs.

Option 1 (guard the dispatch) is what ships in `apply_patch.py`. The `0xc0`
block only sets sampler state (min/mag filter via `glTexParameteri` with
`GL_TEXTURE_MIN/MAG_FILTER`, then anisotropy); it uploads nothing, so skipping
it when the index is invalid just leaves GL default filtering. It stops the
crash and does not blank any texture.

## Final update: the size theory is wrong, and what is actually left

Testing the shipped state (both crash guards, native textures) settled the rest:

- **4096 textures are fine on this Mac.** Worlds 1 (Gaul), 3 (Greece) and 6
  (Rome) keep their 4096-wide atlas terrain and render it correctly. If 4096
  were over the limit, their terrain would fail too. The `GL_MAX_TEXTURE_SIZE`
  size theory at the top of this document is therefore **wrong**: the guard in
  `FUN_1003dec5a` that compares against `glctx[0x444]` is real, but the reported
  max is not below 4096 and it is not what breaks these worlds.

- **Downscaling the atlas textures is a dead end and actively harmful.** The
  `rebake_textures.py` / downscale approach (halve to 2048, reuse mip-1, borrow
  a native footer) produces files that *load* but render **black/incorrect** in
  remastered mode. It corrupts working art and fixes nothing. Do not use it; the
  originals should be left at 4096. (`rebake_textures.py` is kept only as a
  record of the dead end.)

- **What actually remains: invisible slide / pre-fight terrain.** With the crash
  fixed and native textures in place, the three worlds render correctly *except*
  the ground meshes in the "slide" and pre-fight-arena sections, which are
  invisible (you see sky through them) while still being walkable/slidable.
  Established: the geometry's collision loads (objects rest on it, the player
  slides), every level section references the **same** terrain material
  (`Materials/Remaster/rmt_No_roch_sol_S01_P0` plus the grass materials), all
  section streams are intact, and it is invisible in **both** the original and
  remastered graphics modes. Same material renders in some places and not
  others, in both modes, so this is not a texture/atlas/material/shader-mode
  problem. It is a **Mac-specific mesh-rendering issue**: specific terrain meshes
  are dropped from the visual pass (not rasterised) while collision is
  unaffected. Pinning down which meshes and why needs runtime render inspection,
  which is awkward here because attaching `lldb` to this Rosetta process makes
  the running game unable to read its own save files (the save files on disk are
  fine; only the debugged process cannot see them). That is the open frontier.

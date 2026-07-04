# Invisible slide / pre-fight terrain (Normandy / Helvetia / Egypt)

Status: **unsolved.** This is a log of what was tried and ruled out, so nobody
repeats these dead ends. It is separate from, and unrelated to, the crash fixes
in this repo (those work; the game is fully playable).

## Symptom

After the crash fixes, with native (un-downscaled) textures, the three combat
worlds render correctly **except** the ground in the "slide" and pre-fight-arena
sections, which is invisible. You can still walk and slide on it (collision is
present), and objects (crates, enemies, particles) sit on it, but the terrain
mesh itself is not drawn. It is invisible in **both** the original and
remastered graphics modes, and was invisible before any patching.

## This is NOT the texture-size / GL_MAX issue

See [TERRAIN_NOTES.md](TERRAIN_NOTES.md). Worlds 1/3/6 render their 4096 atlas
terrain fine, so 4096 is not too large for this Mac build, and downscaling the
atlas textures only corrupts them. Every terrain section uses the same material
(`Materials/Remaster/rmt_No_roch_sol_S01_P0` plus the grass materials). So the
invisibility is not a texture, atlas, material, or texture-size problem. The
same material renders in some places and not others, in both graphics modes.

## The diagnostic technique used (and why lldb is unusable here)

Attaching `lldb` destabilises this Rosetta process and, as observed repeatedly,
makes the running game unable to read its own save files (the files on disk are
untouched; only the debugged process cannot see them). So interactive debugging
is off the table.

Instead, a debugger-free "register leak via crash report" technique was used:

- A code-cave detour is written into the target function. It copies a chosen
  field or register into an otherwise-unused register (or a stack slot), then
  either continues normally or deliberately faults (`ud2`).
- The OS-generated `.ips` crash report records the full thread state, so the
  leaked value is read back out of a register after the fault.
- One field per crash, no debugger, saves unaffected.

Code caves: the two 16-byte NOP alignment gaps in `__text` are already used by
the crash guards. The whole `__TEXT` segment is `r-x`, so `__gcc_except_tab`
(C++ exception tables) can host a larger probe temporarily and be restored
afterward. That is how the `glGetError` probes below were built.

## The terrain draw path (mapped)

GL is statically imported via lazy stubs, so the draw call sites are findable:

- `_glDrawElements` stub at `0x10112aefe`, called only from `FUN_100f47a6a`
  (general mesh) and `FUN_100f48012` (sprite/quad batches, 6 indices per quad).
- `_glDrawArrays` from `FUN_1008eac22`.

Terrain draws through `FUN_100f47a6a`:

```
glDrawElements(GL_TRIANGLES, count, type, 0)
   type = GL_UNSIGNED_SHORT (0x1403) or GL_UNSIGNED_INT (0x1405), per mesh
   uses a bound VAO + VBO + EBO
```

It has a fallback (a virtual call at `vtable+0x1b0`) taken when the mesh has zero
primitives (`mesh[0xe4] == 0`) or no shader (`renderCtx[0x4a0] == 0`); that path
skips `glDrawElements` entirely.

## What was ruled out (with the test that ruled it out)

| Hypothesis | Test | Result |
|---|---|---|
| Frustum-position culling | crates/enemies in the same view render fine | ruled out: in-view objects are not being culled |
| Sliding-UV system (`_UpdateSlidingUvs`, `FUN_100155f64`) | decompiled | only writes UV scroll offsets; never touches geometry, cannot hide a mesh |
| Texture / atlas / material / graphics-mode | same material as visible terrain; invisible in both modes | ruled out |
| Draw fails with a GL error | `glGetError` probe after every `FUN_100f47a6a` draw | no GL error on any terrain draw anywhere |
| One huge mesh over a driver size limit | probe faulting on `glDrawElements` with count > 100000 | no draw that large at the slide (normal meshes are ~14k indices) |
| Empty-geometry fallback (`mesh[0xe4]==0`) | probe faulting when a mesh takes that fallback | never taken; the slide mesh has real geometry |

## What remains (unresolved)

Two possibilities, both consistent with everything above:

1. **Drawn but degenerate.** The mesh reaches `glDrawElements` and draws without
   error, but its vertices (after transform) produce no visible triangles: all
   clipped, a bad model transform, or a mis-set VAO / vertex buffer.
2. **Bad-bounds culling.** The chunk is in view but its bounding volume is wrong,
   so the frustum test wrongly rejects it. (Bounds are usually cooked data,
   identical to the working Windows build, which argues against this.)

## The wall

Both remaining answers, and any fix, require observing the actual per-mesh render
state at runtime: which draw is the slide chunk, its transformed vertex output,
and its cull decision. Two things block that:

- **Mesh isolation.** The crash-probe cannot single out one terrain mesh among
  the thousands of near-identical terrain draws per frame. They share material,
  size class, and draw path, so there is no distinguishing runtime property to
  key a probe on.
- **No GL frame capture.** There is no working capture for x86 OpenGL under
  Rosetta on macOS: RenderDoc does not support macOS OpenGL, and apitrace would
  need an x86 build intercepting the deprecated GL framework. So the draws,
  vertices, and cull state cannot be inspected.

The frustum culler (`oCFrustumRenderCuller`) was located, but the methods
reachable through its vtable are boilerplate (destructors, serialize, refcount);
the runtime visibility test was not cleanly locatable to force "always visible"
as a culling test, and a blind global cull-disable risks crashing the game
rather than testing the hypothesis.

## For anyone continuing

1. **A GL frame capture of the slide area is the clean path.** Seeing which draws
   are issued there, the bound vertex buffer, and the transformed output
   immediately distinguishes "not drawn" from "drawn-degenerate" and points at
   the exact cause.
2. Or use the game's own cooking / editor tools (the OSome "oC" engine) to
   re-export the slide and arena meshes.
3. If continuing the binary route: instrument the geometry component render
   (`oCGoCptGeometry`) rather than the shared `glDrawElements` site, and use the
   sliding-UV flag as the one available handle to isolate slide meshes from
   ordinary terrain (`_UpdateSlidingUvs` runs only on slide geometry).

The game is fully playable start to finish; this is a cosmetic issue confined to
the slide and pre-fight sections of the three combat worlds.

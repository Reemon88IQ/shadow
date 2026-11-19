# Blender scenes in Shadow

Shadow renders `.blend` files the same way as HTML scenes: drop them in the queue (or `shadow_render` / the CLI), and you
get `<Save to>\<scene>\<scene>.mp4` (`_shorts.mp4` for 9:16), 30 fps, x264 crf 16, AAC 192k with the audio inside the QC
limits, a `.qc.txt`, an optional thumbnail, and a failure log on error. Mixed queues (HTML + .blend) are fine.

Engine: `blender_core.py` (no GUI) + `tools/blender/shadow_blender_job.py` (runs inside Blender). Blender runs headless with
your own preferences and add-ons; the .blend is opened read-only (never saved). Each finished frame is piped into ffmpeg and
deleted while the next renders. Cancel kills Blender; nothing is left behind. `render_core.py` is not changed.

Needs: Blender (free; found in PATH, `Program Files\Blender Foundation\Blender X.Y`, Steam, the Store, or `SHADOW_BLENDER`).
Tested with Blender 5.3 + RTX 5070 Ti (OptiX). "Install missing" offers `winget install BlenderFoundation.Blender` when a
.blend is in the queue and Blender is not found. A queue with only .blend scenes does not need Chrome.

## Settings
| Shadow | For a .blend |
|--------|-------------|
| Quality Draft / Good / Best | 1280 / 1920 / 1920 px wide. Samples: Eevee 16 / 64 / max(scene, 128); Cycles 32 / 128 / max(scene, 512) + denoise (OptiX on NVIDIA, else OpenImageDenoise); Workbench AA 8 / 16 / 32. Best never lowers what you set in the scene |
| Blender (new option) | Scene setting (default) / Eevee / Cycles (GPU) / Workbench (fast) |
| Format 16:9 / 9:16 / Auto | Auto: `short_*` / `*_short` names -> 9:16. 9:16 uses a camera named like `Camera 9x16`, `... vertical`, `... portrait`, `... short` (or scene property `shadow_camera_9x16`); without one the 16:9 camera is centre-cropped (keeps its height of view) and a warning says so |
| Sound Generated | the scene's own sound strips / speakers, mixed down by Blender (none -> silent, like an HTML scene without SOUND) |
| Sound Auto / External | `<scene>.mp3/.wav/...` next to the .blend / the picked file; loudnorm + limiter like HTML. A `<scene>.wav` next to a .blend is always its song (no word captions for .blend) |
| Capture PNG / Fast | Blender writes PNG (lossless) or JPEG 95 frames |

Also: the scene's frame range; other frame rates are re-timed to 30 fps (same length; 24 fps -> 60 frames for 2 s, measured);
thumbnail from scene property `THUMBNAIL_T` (seconds) or a timeline marker named `thumbnail`; motion blur, compositor,
colour management, add-ons all as in the scene. Python drivers/scripts in the .blend do NOT run (Blender's `-Y`), so a file
from the internet cannot run code; `allow_scripts=True` in `blender_core.render` turns them on.

## Checks before a render
`python tools/check_frames.py scene.blend [--engine eevee] [--times 1,50%,end-1]` (or `shadow_check_frames`): Blender renders
those frames at 640 px with draft samples in 16:9 and 9:16 -> `<scene>.check.jpg`. Missing textures / linked files / sounds
are PROBLEMS (exit 1); "no 9:16 camera" is a note. No purity check (Blender is deterministic).

## Not supported (yet)
- Narration with word captions (HTML-only); for a .blend put the voice in its sound (a sound strip, or `<scene>.wav`).
- Rendering several scenes of one .blend in one go (the active scene is used; `scene` in the job picks another, not in the GUI).
- Network render / render farms.

Tests: `python tests/test_blender.py` (29 checks; builds its .blend files in a temp folder with `tests/fixtures/make_blend_fixture.py`).

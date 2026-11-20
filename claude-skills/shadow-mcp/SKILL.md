---
name: shadow-mcp
description: "Render Shadow scenes (three.js / canvas .html AND Blender .blend -> MP4 1080p 30 fps with sound, 16:9 and 9:16 Shorts) from a Claude session through the local Shadow MCP server (tools named shadow_* / mcp__shadow__*), without opening the Shadow window. Use whenever the user asks to render, re-render, batch-render a folder, preview/check frames, read a QC report, find the output files, or cancel/follow a Shadow render."
---

# Shadow MCP

The server is `<your Shadow folder>\tools\mcp_server.py` (local, stdio, free, no API keys). It imports Shadow's own
`app.py` / `render_core.py` and renders with the GUI queue's function `app.render_scene`, so the result is the same as
pressing Render in Shadow with the same settings. The Shadow window does not need to be open. Setup: `docs/MCP.md`.

If the `shadow_*` tools are not in the session (deferred), load them with ToolSearch `+shadow`. If they are not there at
all, the server is not connected: say so and point to `docs/MCP.md` (do not change settings without asking).

## When to use which tool
| Want | Tool |
|------|------|
| Is everything installed? default Save to? | `shadow_info` |
| Before EVERY full render of a new or edited scene | `shadow_check_frames(scene)` - look at the returned sheet image; fix page errors / impure seek(t) first |
| See what a batch will do (format, audio, narration, output path) | `shadow_plan(paths, ...)` - renders nothing |
| Render one scene, several, or a whole folder | `shadow_render(paths, ...)` -> job_id (returns at once) |
| Follow it | `shadow_progress(job_id, wait_seconds=50)` repeatedly until state is finished/cancelled |
| Stop | `shadow_cancel(job_id)` = Stop queue; `current_only=True` = Cancel current (the queue goes on) |
| Quality report | `shadow_qc(mp4 or folder)`; `rerun=True` checks an mp4 again |
| What was made | `shadow_outputs(job_id=...)` or `shadow_outputs(path=folder)` |
| Which YouTube channels are signed in | `shadow_youtube` (Shadow 3.14+) |
| What an upload would send | `shadow_upload_preview(video, scene=..., ...)` - sends nothing |
| Upload to YouTube | `shadow_upload(same arguments)` -> upload_id, then `shadow_upload_progress(upload_id, wait_seconds=50)` |
| Stop an upload | `shadow_upload_cancel(upload_id)` |

Uploading publishes to the user's channel: ALWAYS call `shadow_upload_preview` first, show the channel, title, privacy,
AI label, publish time and thumbnail, and call `shadow_upload` only after the user says yes to that preview. Uploads are
Private by default; until the user's Google project passes YouTube's API audit every API upload stays Private. A channel
is signed in once in the Shadow window (YouTube... -> Sign in...): never ask for a password.

## Settings (same names as the GUI)
- `quality`: `best` (1080p, 64 samples, default) | `good` | `draft` (720p, quick looks) | `1440p` | `4k` (Shadow 3.15+).
- `format`: `auto` (default: `short_*`, `*-short`, `*_short`, `* short`, `...shorts` -> 9:16, the rest 16:9) | `16:9` | `9:16`.
- `sound`: `auto` (default: `<scene>.mp3/.wav/.m4a/.flac/.ogg/.aac` next to the scene, else the scene's own sound + warning)
  | `generated` | `none` | `file` + `audio_file`.
- `capture`: `png` (lossless, default) | `jpeg` (about 1.2x faster since 3.15, slightly softer, bigger file - fine for drafts).
- `narration`: `auto` (default: `<scene>.wav` + `<scene>.words.json` next to the scene are attached, style pop; sound is
  forced to generated) | `off`. Scenes with narration built in show `[voice embedded]` and are never narrated twice.
- `outdated`: `skip` (default) | `render` - 9:16 scenes that do not read `get('h')`. The GUI asks; here ask the user if it matters.
- `skip_up_to_date`: `false` (default) | `true` - keep scenes whose `<scene>.mp4` is newer than the scene and its song /
  narration files (status `Skipped (up to date)`; the GUI's "Skip scenes already rendered"). Use it for re-running a folder batch.
  `shadow_plan` / `shadow_progress` also give `estimate_s` / `eta` ("about 12 min · done ~15:40"); a narrated scene gets `<video>.srt`.
- `out_dir`: default = the GUI's "Save to" (~/.shadow_renderer.json). Output: `<out_dir>\<scene name>\<scene>.mp4`
  (`<scene>_shorts.mp4` for 9:16) + `.qc.txt` (+ `_thumb.jpg` if the scene sets THUMBNAIL_T, + `<scene>_<time>.log` on failure).
  Nothing is overwritten (`_2`, `_3`).

## Blender (.blend) scenes
- Same tools, same output. Extra settings: `engine` = `scene` (default, the file's own) | `eevee` | `cycles` (GPU when
  available) | `workbench` (fast previews); `device` = `auto` | `gpu` | `cpu`.
- 9:16 needs a camera named like `Camera 9x16` (or ... vertical / portrait / short); without one Shadow centre-crops the 16:9
  camera and warns - tell the user and offer to add the camera.
- Sound: `generated` = the file's own sound strips / speakers; `auto` = `<scene>.mp3/.wav` next to the .blend.
- check_frames on a .blend renders draft frames with Blender (slower than HTML: ~10-60 s); missing textures are problems.
- No word-caption narration for .blend. Python drivers in the file do not run (safety); say so if a driver-based rig looks frozen.
- Details: `docs/BLENDER.md`.

## Rules
- Run `shadow_check_frames` first; a full Best render of a 3-minute scene can take around 10 minutes.
- Use `wait_seconds=50` in `shadow_progress` (max 55); do not call it every few seconds.
- Jobs run one at a time; a second `shadow_render` queues behind the first (`queued_behind`).
- After it finishes: report each file's path, size (WxH), duration, QC status and every warning. QC WARN/FAIL -> read the
  report (`shadow_qc`) and say what failed (loudness, true peak, black, frozen, size, duration).
- Error rows carry `error` and `log` (the failure log in the scene folder) - read the log before guessing.
- Do not run the Shadow GUI and an MCP render at the same time on big scenes (they share the GPU; both work, both get slower).
- The log is `~/shadow.log` (same as the GUI).

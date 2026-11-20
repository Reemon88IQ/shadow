# Shadow user guide

Everything in the Shadow window, what each setting does, where your files go, and what to do when something goes wrong.
New to Shadow? Start with [Quick start](../README.md#quick-start), then come back here.

- [Install, update, uninstall](#install-update-uninstall)
- [The window](#the-window)
- [The scenes table](#the-scenes-table)
- [Buttons under the table](#buttons-under-the-table)
- [Options](#options)
- [Rendering](#rendering)
- [What you get](#what-you-get)
- [The quality check (QC)](#the-quality-check-qc)
- [Sound](#sound)
- [Narration and captions](#narration-and-captions)
- [Shorts (9:16)](#shorts-916)
- [Blender scenes](#blender-scenes)
- [Assistant panel and Claude](#assistant-panel-and-claude)
- [Command line](#command-line)
- [Settings, logs and privacy](#settings-logs-and-privacy)
- [Troubleshooting](#troubleshooting)

---

## Install, update, uninstall

**Install.** Download `Shadow-<version>-Setup.exe` from [Releases](../../../releases/latest) and run it. It installs for
your Windows user only (no admin), to `%LOCALAPPDATA%\Shadow`, with a Desktop and a Start menu shortcut. The installer is
in English or Arabic. Windows SmartScreen may warn because the installer is not code-signed: choose *More info* → *Run anyway*
(the release page lists the file's SHA-256 so you can check it).

**What Shadow needs.** Google Chrome (Shadow draws your scenes in it) and ffmpeg (encoding and the quality check). If one is
missing, the installer offers to install it with winget, and the top bar in Shadow shows it in red with an
*Install missing* button. Blender is optional and only needed for `.blend` scenes.

**Update.** About 2 seconds after Shadow opens it asks GitHub for the latest release. When a newer one exists, the button in
the top-right corner turns into **Update to vX.Y**: press it, confirm, and Shadow downloads the installer (checking its size
and SHA-256), installs it and opens again by itself. You can also press **Check for updates** at any time. Your settings,
logs and videos are kept. The update waits while a render is running.

**Uninstall.** Windows *Settings → Apps → Installed apps → Shadow → Uninstall*. Your settings file and log
(see [Settings, logs and privacy](#settings-logs-and-privacy)) and your videos are left in place; delete them by hand if you want.

## The window

**Top bar**

| Item | What it does |
|---|---|
| **Check for updates** | Asks GitHub for a newer version (see above). Becomes **Update to vX.Y** when there is one. |
| **Light / Dark** | Switches the theme. Shadow follows Windows until you press it, then remembers your choice. |
| **Assistant** | Opens the chat panel on the right (see [Assistant panel](#assistant-panel-and-claude)). |
| **Chrome · ffmpeg · playwright · Blender** | Green = found, red = missing. Blender is only needed for `.blend` files. |
| **Re-check** | Looks again (for example after installing ffmpeg). |
| **Install missing** | Installs what is missing with winget in a new window. Restart Shadow afterwards. |

## The scenes table

Drop `.html` or `.blend` files (or whole folders) onto the drop area, or click it to browse. Each scene is one row:

| Column | Meaning |
|---|---|
| **Name** | The scene file. `[voice]` = narration attached, `[voice embedded]` = narration already inside the file (never added twice). |
| **Status** | `Queued`, the current step (`Loading scene`, `Rendering sound`, `Frames 120/900`, `Encoding MP4`), then `Done` (+ `· QC OK / Warn / Fail`, `· thumb`), `Done (silent)`, `Skipped (up to date)`, `Skipped (outdated, no 9:16)`, `Cancelled` or `Error`. |
| **Time** | Before rendering: an estimate. While rendering: the time left. After: how long it took. |
| **Duration** | The video length once it is rendered. |
| **Format** | 16:9 or 9:16 for this row (see [Shorts](#shorts-916)). |
| **Audio** | What sound this row will use: a song file name, narration, the scene's own sound, or silent. |

Under the progress bar, Shadow shows the queue estimate, e.g. *about 12 min · done ~15:40*. It starts from a rough guess
(frames × measured speed) and corrects itself with the real speed after the first frames.

You can add and remove rows while a batch renders: new rows render after the ones before them. Double-click a row to
preview it, and press Delete to remove it.

## Buttons under the table

| Button | What it does |
|---|---|
| **Add...** | Pick scene files. |
| **Add folder...** | Adds every `.html` / `.blend` in a folder. If scenes are only in subfolders, Shadow asks before adding them. Dropping a folder does the same. |
| **Remove** / **Clear** | Remove the selected rows / all rows (a running render is stopped first). |
| **Preview** | Opens the scene in your browser (a `.blend` opens in Blender). |
| **Check frames** | A quick look before a long render: a contact sheet of frames in 16:9 and 9:16, page errors, and whether the scene draws the same frame twice for the same time. Takes seconds. Results in a small window; the sheet is saved as `<scene>.check.jpg` next to the scene. |
| **Narration...** | Attach a voice and word timings to the selected scene (see [Narration](#narration-and-captions)). |

## Options

| Option | Choices |
|---|---|
| **Quality** | *Best (1080p, 64 samples)* - the default for uploads. *Good (1080p, 24 samples)* - faster, slightly less smooth effects. *Draft (fast, 720p)* - quick looks only. "Samples" is passed to the scene; scenes use it for antialiasing and effects. |
| **Format** | *16:9 (YouTube)*, *9:16 (Shorts)*, or *Auto (by file name)* - decided per file (see [Shorts](#shorts-916)). |
| **Sound** | *Generated (built into the scene)*, *None (silent)*, *External file...*, *Auto (file next to scene)* (see [Sound](#sound)). |
| **Capture** | *Best quality (PNG)* - lossless frames, the default. *Fast (JPEG, ~2x faster)* - measured 2.7x faster frame stage on a 1080p scene, a very slightly softer picture and a ~1.7x larger file. Good for drafts. |
| **Blender** | For `.blend` files only: *Scene setting*, *Eevee*, *Cycles (GPU)*, *Workbench (fast)*. |
| **Save to** | The folder where every scene gets its own subfolder (see [What you get](#what-you-get)). |
| **Open folder when done** | Opens the output folder after the batch. |
| **Skip scenes already rendered (up to date)** | On by default. A scene whose video is newer than the scene and its song / voice files (and has the right size) is not rendered again; the row says *Skipped (up to date)*. Change the scene and it renders again. Note: switching only between Good and Best (same size) is not detected - turn the option off for that. |
| **Notify me when the queue ends** | A Windows notification with how many videos were done or failed; click it to open the folder. Not shown when you cancel or when everything was skipped. |

## Rendering

**Render** starts the queue from the top. **Cancel current** stops only the scene being rendered (the queue continues with
the next one). **Stop queue** stops everything. A cancelled or failed render never leaves a half-written video: Shadow
writes `<name>.part.mp4` and only renames it when everything succeeded.

How it works: Shadow serves the scene's folder on a local address, opens it in your installed Chrome (in the background),
renders the sound first, then asks the scene for one frame at a time (`seek(t)`) and pipes the frames straight into ffmpeg,
which encodes while the next frames are drawn. Nothing depends on how fast your PC is - a slow PC takes longer and makes
the same video.

## What you get

For a scene `C:\Scenes\ocean.html` and *Save to* `D:\Videos`:

```
D:\Videos\ocean\
  ocean.mp4            1920x1080, 30 fps, H.264 (x264 CRF 16) + AAC 192 kbps
  ocean.qc.txt         the quality report
  ocean_shorts.mp4     if it was also rendered 9:16 (1080x1920), with ocean_shorts.qc.txt
  ocean.srt            subtitles, if the scene was narrated
  ocean_thumb.jpg      1280x720 thumbnail, if the scene sets window.THUMBNAIL_T
  ocean_<time>.log     only if the render failed: what went wrong
```

A Short made from its own scene file (`short_ocean.html`, `ocean-short.html`...) is saved in the same folder as the video:
`ocean\ocean_shorts.mp4`.

**Folder** (under Save to) picks the main folder: *Auto (Music / Video)* decides per scene - a scene that plays a song file
next to it goes to `D:\Videos\Music\ocean\`, everything else to `D:\Videos\Video\ocean\`. Choose *Music*, *Video*, your
own folder (**New folder...**), or *None* to put scene folders straight into Save to. A scene can also name its folder in
its `<head>`: `<meta name="shadow-folder" content="Music">` (Auto follows it; nested names like `Music/2026` work too).

Existing files are never overwritten: a second render becomes `ocean_2.mp4`, `ocean_3.mp4`... (unless *Skip scenes
already rendered* skips it). A folder created for a scene that was cancelled before anything was written is removed again.

## The quality check (QC)

After every render Shadow measures the video and writes `<name>.qc.txt`. The row shows **QC OK**, **QC Warn** or **QC Fail**;
the report lists every reason.

| Check | Limit |
|---|---|
| Resolution | must match the chosen quality and format |
| Duration | Warn when it differs from the scene's length by more than 0.3 s, Fail above 1 s |
| Audio | an audio track must exist (unless the video is silent on purpose) |
| Loudness | -17 to -13 LUFS (Shadow aims for -15) |
| True peak | at most -1 dBTP |
| Black frames | Warn for black longer than 0.5 s (the first half second is ignored, for fade-ins) |
| Frozen picture | Warn when the picture does not change for more than 5 s |

QC only adds information: it never changes or deletes your video.

## Sound

| Mode | What it uses |
|---|---|
| **Generated (built into the scene)** | The sound the scene makes itself (its `STYLE_SOUND` score). A scene without one renders silent and the row says *Done (silent)*. |
| **None (silent)** | No audio track. |
| **External file...** | One song or voice file you pick, for every scene in the batch. |
| **Auto (file next to scene)** | Each scene uses its own file with the same name next to it: `<scene>.wav`, `.mp3`, `.m4a`, `.flac`, `.ogg` or `.aac` (first one found). So one batch can render several songs. No file → the scene's own sound + a warning. `<scene>.wav` together with `<scene>.words.json` counts as narration, not a song. |

Songs are loudness-matched to about **-14 LUFS** (YouTube's own playback level). That covers a song file used as the sound,
or a scene in the Music folder. Narration and the scene's own sound are matched to **-15 LUFS**. A scene can choose its own
level with `<meta name="shadow-lufs" content="-16">` (-24 to -9). The true peak always stays under -1 dBTP. All of this is
measured *after* the AAC encode, so loud masters pass the quality check. Shadow only ever turns a track down, never up.

## Narration and captions

Put two files next to the scene with the same name: `<scene>.wav` (the voice) and `<scene>.words.json` (word timings,
`{"words": [{"w": "Hello", "s": 0.32, "e": 0.61}, ...]}`). Shadow attaches them by itself and marks the row `[voice]`.
Or select a row and press **Narration...** to pick the files, the caption style (pop or karaoke) and whether to show
word captions.

- The voice is mixed into the scene's own sound, so narrated scenes always render with *Generated* sound (Shadow tells you
  if another mode was selected).
- Word captions are drawn on 2D canvas scenes. WebGL / three.js scenes get the voice only (Shadow warns).
- Every narrated video also gets a `.srt` subtitle file from the same word timings - ready to upload to YouTube.
- Need word timings for a voice recording? `python tools\narrate\make_words.py voice.wav script.txt` (free, offline,
  faster-whisper) writes `voice.words.json` and `voice.srt`; with the script, your exact words get the voice's timings.

## Shorts (9:16)

With **Format = Auto (by file name)**, a scene renders 9:16 when its file name starts with `short_` or ends with
`-short`, `_short` or ` short` (also `...shorts`, any case) - for example `short_ocean.html` or `ocean-short.html`.
Everything else renders 16:9. So one queue can make the video and its Short together; the Short is saved as
`<scene>_shorts.mp4` in the same folder.

A scene must read the `h` URL parameter and lay itself out for a tall frame to support 9:16. Before rendering 9:16,
Shadow checks each scene and asks what to do with scenes that do not (they would look like a squeezed 16:9).

## Blender scenes

`.blend` files go in the same queue and get the same sound modes, Shorts, QC and folders. Shadow runs your installed
Blender in the background (no window) with the engine you pick under **Blender**. For 9:16, add a camera named like
`Camera 9x16`; without it Shadow crops the middle of the 16:9 camera and warns. Details: [BLENDER.md](BLENDER.md).

## Upload to YouTube

**YouTube...** (next to *Open log*) uploads the videos you just rendered to one of your channels, with the title,
description, tags and *Made for Kids* from a `metadata.txt` next to the video or the scene. Sign in once per channel in
your browser; uploads are Private by default. It needs a free one-time Google Cloud setup, and until that project passes
YouTube's API audit, uploads stay Private: [YOUTUBE.md](YOUTUBE.md).

## Assistant panel and Claude

Press **Assistant** to open a chat on the right. Ask in plain words - "render this folder and check the quality",
"check the frames of ocean.html" - and watch Claude's steps and answer as they arrive. **Stop** interrupts, **New chat**
starts over, **Folder...** chooses the folder Claude may read.

It uses **your own Claude subscription through the installed Claude Code** (no API key, no extra cost). Claude can only
read files in that folder and use Shadow's tools; stopping a render needs your **Allow once**. Setup and the exact
permissions: [ASSISTANT.md](ASSISTANT.md).

The same tools are available to Claude Code, Claude Desktop and other AI apps that support MCP (Cursor, VS Code,
Windsurf, Gemini CLI): [MCP.md](MCP.md).

## Command line

```
Shadow.exe --render scene.html out.mp4 [--vertical] [--preset draft|good|best] [--silent] [--fast] [--engine ...]
Shadow.exe --check scene.html [--only 16:9|9:16] [--times ...] [--out sheet.jpg] [--engine ...]
```

`--render` writes exactly the path you give (no scene folder) plus its QC report; the exit code is 0 when the video has
the expected size. `--fast` = Fast (JPEG) capture. `--engine` (for `.blend`): `scene`, `eevee`, `cycles` or `workbench`.
`--check` makes the contact sheet and purity check without rendering.

## Settings, logs and privacy

- Settings: `%USERPROFILE%\.shadow_renderer.json` (theme, Save to, options).
- Log: `%USERPROFILE%\shadow.log` - press **Open log**. A failed render also leaves `<scene>_<time>.log` in its folder.
- **What goes online:** the update check (GitHub's public releases API, no account), whatever your scenes load themselves
  (for example three.js from a CDN), and - only when you use the Assistant - your messages to Claude through Claude Code.
  Nothing else is sent anywhere: no account, no analytics.

## Troubleshooting

| Problem | What to do |
|---|---|
| Windows says "Windows protected your PC" | The installer is not code-signed. *More info* → *Run anyway*. |
| "ffmpeg (missing)" right after installing it | Sign out and in once (or restart) so Windows refreshes the PATH, then *Re-check*. |
| A row says **Error** | Read `<scene>_<time>.log` in its folder (or *Open log*). The first line of the error is also shown in the message box. |
| "These scenes miss something Shadow needs" (before rendering) | Shadow read the scene and found no `window.__ready`, `window.seek(t)`, `window.DURATION` or `<canvas id="c">` (see [Make a scene](../README.md#make-a-scene)). **No** skips those scenes, **Yes** tries anyway (the check only reads the text, so a scene built in an unusual way can still work). |
| "The scene cannot render: ..." | The scene loaded, but the part named in the message is missing or wrong (for example `DURATION` is text, not a number). |
| "The scene did not load" | The scene never set `window.__ready = true` in time, or has a script error (shown in the message). Open it with **Preview** and look at the browser console, or run **Check frames**. |
| "ffmpeg failed: ..." | The message shows ffmpeg's last 3 lines. A full disk or a broken song file are the usual causes. |
| The video jumps or flickers | The scene is not a pure function of time (a real-time loop or `Math.random`). **Check frames** reports this. |
| 9:16 looks squeezed | The scene does not read `h` - see [Shorts](#shorts-916). |
| QC Warn: true peak / loudness | Usually a very loud master; Shadow already turns it down. Re-render with the current version; if it stays, open an issue with the `.qc.txt`. |
| The update did not install | Close Shadow and run the new `Shadow-<version>-Setup.exe` from Releases by hand. Setup logs are in `%TEMP%\Setup Log *.txt`. |
| Still stuck | [Open an issue](../../../issues/new/choose) with the version, steps and the log. |

Writing scenes: [Make a scene](../README.md#make-a-scene) and the minimal example
[`tests/fixtures/webgl_min.html`](../tests/fixtures/webgl_min.html).

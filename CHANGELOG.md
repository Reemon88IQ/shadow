# Changelog

Every release is on the [Releases](../../releases) page with its `Shadow-<version>-Setup.exe`. Installed copies update
themselves: Shadow shows **Update to vX.Y** when a new release is out.

## 3.10 - 2026-11-19
- **Blender scenes:** add `.blend` files to the queue like `.html` scenes. Shadow runs your installed Blender in the
  background (Eevee, Cycles on the GPU, or Workbench) and uses the same encoding, sound (Auto), Shorts (a 9:16 camera picked
  by name) and quality check. Other frame rates are re-timed to 30 fps. Guide: [docs/BLENDER.md](docs/BLENDER.md).
- **Claude connection (MCP):** Claude can render scenes or folders, follow the progress, cancel, preview frames and read the
  quality report without opening the window; the result is the same as rendering in the app. Guide: [docs/MCP.md](docs/MCP.md).
- 3.8 and 3.9 were internal steps and were never released; their changes are in 3.10.

## 3.7 - 2026-11-18
- In-app updates install on the first try: the installer now waits until the old Shadow has fully closed (before, the
  first attempt could stay on the old version).

## 3.6 - 2026-11-18
- Loud songs pass the quality check: loudness and true peak are measured after the AAC encode and the track is turned
  down exactly as much as needed (never up) - about -15 LUFS and under -1 dBTP.
- Thumbnails: a scene that sets `window.THUMBNAIL_T` gets `<name>_thumb.jpg` (1280x720, or 720x1280 for Shorts).

## 3.5 - 2026-11-18
- Format Auto also renders 9:16 for names ending in `-short`, `_short` or ` short` (not only `short_` at the start).

## 3.4 - 2026-11-18
- Faster renders: frames go straight into ffmpeg (no frame files on disk), and the new **Capture** option *Fast (JPEG)*
  is about 2x faster with a very slightly softer picture. *Best quality (PNG)* stays the default.

## 3.3 - 2026-11-18
- Fixed gray stripes left between the panels when switching from Light to Dark.

## 3.2 - 2026-11-18
- Fixed: after an in-app update Shadow did not reopen ("Failed to load Python DLL").

## 3.1 - 2026-11-18
- The Light theme is a soft light gray instead of near-white.

## 3.0 - 2026-11-18 - first public release
- Installer: per-user install (no admin), Desktop and Start menu shortcuts, uninstall from Windows Apps; installs Google
  Chrome and ffmpeg with winget if they are missing.
- In-app updates: a *Check for updates* button; new releases install themselves.
- Render queue, 16:9 and 9:16 (Format Auto by file name), sound modes, narration with word captions, and a quality check
  report for every video.

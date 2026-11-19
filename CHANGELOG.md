# Changelog

Every release is on the [Releases](../../releases) page with its `Shadow-<version>-Setup.exe`. Installed copies update
themselves: Shadow shows **Update to vX.Y** when a new release is out.

## 3.12
- **Upload to YouTube** (**YouTube...** button): sign in once per channel in your browser, then upload the videos you just
  rendered - title, description (chapters), tags and Made for Kids from `metadata.txt`, Private by default, resumable with
  progress, a link to YouTube Studio when done. Uses the free YouTube Data API with your own Google Cloud project
  (setup in [docs/YOUTUBE.md](docs/YOUTUBE.md)); until that project passes YouTube's audit, uploads stay Private.
- **A Short and its video share one folder**: `short_ocean.html` (or `ocean-short.html`) rendered 9:16 now goes to
  `ocean\ocean_shorts.mp4`, next to `ocean\ocean.mp4`, instead of its own `short_ocean\` folder.
- **Folder option** (next to Save to): *Auto (Music / Video)* sorts every video into a main folder by itself - a scene that
  plays a song file next to it goes to `Music\`, everything else (own sound, narration, silent) to `Video\`. Or pick
  *Music*, *Video*, a folder you add with **New folder...**, or *None* (straight in Save to, like before).
- A scene can choose its folder with `<meta name="shadow-folder" content="Music">` (Auto follows it; nested like `Music/2026` works).

## 3.11
- **Check frames** button: select one or more scenes and get a contact sheet (16:9 and 9:16) in seconds, with page errors
  and frames that change between two draws of the same time (`seek(t)` not pure) listed - before a long render.
  Also `Shadow.exe --check scene.html`.
- **Time estimate**: a Time column (about how long each scene takes, the time left while it renders, how long it took) and
  "about 12 min · done ~15:40" for the whole queue, corrected with the speed measured while rendering.
- **Skip scenes already rendered** (on by default): a scene whose video is newer than the scene and its song / voice files
  is marked *Skipped (up to date)* instead of being rendered again as `_2`. Change the scene and it renders again.
- **Add folder...**: adds every `.html` / `.blend` scene in a folder (subfolders on request); dropping a folder works too.
- **Subtitles**: every narrated video gets a `.srt` file next to it, from the same word timings as the captions.
- **Assistant panel** (button *Assistant*): chat with Claude inside Shadow ("render this folder and check the quality")
  and watch its steps and reply as they arrive. It uses your Claude subscription through the installed Claude Code CLI;
  there is no API key. Claude can only read files and use Shadow's tools. Stopping a render needs your *Allow once*.
  Guide: [docs/ASSISTANT.md](docs/ASSISTANT.md).
- **Other AI clients:** setup for Claude Desktop, Cursor, VS Code (Copilot agent mode), Windsurf and Gemini CLI in
  [docs/MCP.md](docs/MCP.md), and an optional HTTP mode for the MCP server (`--http`). It listens on localhost only and is
  off by default.
- **Notification** when the queue ends (done / failed counts); click it to open the output folder. Can be turned off.

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

# Shadow MCP server

`tools/mcp_server.py` lets Claude (Claude Code, Claude Desktop, any MCP client) use Shadow directly: render scenes or whole
folders, follow progress, cancel, run check_frames, read QC reports, list outputs. Local and free: stdio transport, no network,
no API keys. It does not need the Shadow window open. It renders with the GUI queue's own function (`app.render_scene`, which
`App._run_one` also calls), so the output files, folders, QC reports and warnings are the same as the GUI's.

Requirements: a copy of this repository (the source, not only the installed Shadow.exe - the server does not run inside
the exe), Python 3.12 with `mcp` (2.x), `playwright`, `Pillow` (for check_frames), plus Google Chrome and ffmpeg on PATH,
the same as running Shadow from source. Below, `C:\Shadow` stands for the folder you cloned it into - use your own path.

## Set it up (once)
```
git clone https://github.com/Reemon88IQ/shadow C:\Shadow
python -m pip install "mcp>=2" playwright pillow tkinterdnd2 sv-ttk darkdetect numpy
```
Chrome: install Google Chrome normally (Shadow renders with the installed Chrome). ffmpeg: `winget install -e --id Gyan.FFmpeg`
(open a new terminal afterwards so it is on PATH).
Optional, so Claude knows when and how to use the tools: copy `claude-skills\shadow-mcp` to `%USERPROFILE%\.claude\skills\shadow-mcp`.

## Connect it to Claude Code
Every session, every folder (user scope):
```
claude mcp add shadow --scope user -- python C:\Shadow\tools\mcp_server.py
```
Only inside this repo (project scope; writes `.mcp.json` into the repo, the same command with `--scope project`).
Check: `claude mcp list` should show `shadow: ... - Connected`. In a session, `/mcp` lists the 9 tools.
Remove: `claude mcp remove shadow --scope user`.

Claude Desktop instead: add this to `%APPDATA%\Claude\claude_desktop_config.json` under `"mcpServers"`, then restart it:
```json
"shadow": { "command": "python", "args": ["C:\\Shadow\\tools\\mcp_server.py"] }
```

The skill in `claude-skills/shadow-mcp/SKILL.md` (copied to `~/.claude/skills/shadow-mcp/`) tells Claude when and how to use the tools.

## Tools
| Tool | What |
|------|------|
| `shadow_info` | version, Chrome / ffmpeg / playwright found, default Save to, settings choices, last job |
| `shadow_plan(paths, ...)` | dry run: each scene's Format, Audio, narration mark, output path, skipped outdated 9:16 scenes, warnings |
| `shadow_render(paths, out_dir, quality, format, sound, audio_file, capture, narration, outdated, recursive)` | starts a job, returns `job_id` at once |
| `shadow_progress(job_id, wait_seconds)` | state, percent, each scene's status text (`Frames 120/900`, `Done · QC OK`), ETA, outputs, QC, warnings; `wait_seconds` (max 55) blocks until the job ends |
| `shadow_cancel(job_id, current_only)` | Stop queue / Cancel current |
| `shadow_jobs` | jobs of this server session |
| `shadow_check_frames(scene, only, times, show_sheet)` | `tools/check_frames.py`: contact sheet (returned as an image), page errors, seek(t) purity |
| `shadow_qc(path, rerun, quality)` | read `.qc.txt` for an mp4 / folder; `rerun` checks the mp4 again |
| `shadow_outputs(path or job_id)` | videos (size, duration, audio, QC status), QC reports, logs, thumbnails |

Blender `.blend` scenes work in every tool (paths may mix .html and .blend); `shadow_render` / `shadow_plan` also take
`engine` (`scene|eevee|cycles|workbench`) and `device` (`auto|gpu|cpu`), `shadow_check_frames` takes `engine`. See docs/BLENDER.md.

Settings use the GUI's choices: quality `best|good|draft`, format `auto|16:9|9:16`, sound `auto|generated|none|file`,
capture `png|jpeg`, narration `auto|off`, outdated `skip|render` (the GUI asks in a dialog; the server cannot, default skip).
Defaults: best, auto, auto, png, auto, skip; out_dir = the GUI's Save to.

## Notes
- Jobs run one at a time on one background thread; a second job waits. Jobs live as long as the server process (the client
  restarts it per session); the files stay.
- MCP clients start the server with a cut-down environment (no `PROGRAMFILES`). The server puts back the standard Windows
  folders, otherwise Chrome is not found (measured: `Missing: chrome`).
- Logs go to `~/shadow.log` (`SHADOW_LOG` env overrides it, used by the tests). Nothing is printed to stdout (the MCP channel).
- Test: `python tests/test_mcp_server.py` (fixtures only; includes a real stdio session and, if Chrome is installed, a real draft render).

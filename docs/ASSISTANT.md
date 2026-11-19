# Assistant panel

Press **Assistant** in Shadow's top bar to open a small chat on the right side of the window. You type a request, for
example *render this folder and check the quality*. Claude answers in the panel and you watch its steps there
(`> shadow_render(...)`, `> shadow_progress(...)`). It uses the same tools as the [MCP server](MCP.md): render, plan,
progress, check frames, QC, outputs.

It runs on your **Claude subscription** (Pro / Max) through the **Claude Code** CLI installed on your PC. There is no API
key and no pay-per-use API. Shadow starts `claude` in headless (print) mode in the background, so you never see a
terminal window.

## What you need
- Claude Code, signed in with your Claude account:
  ```powershell
  irm https://claude.ai/install.ps1 | iex
  claude auth login
  ```
  The panel checks this with `claude auth status`. If Claude Code is missing or not signed in, the panel says what to
  run. It also refuses to start when Claude Code is signed in with an API key or a cloud provider (paid per use), and it
  never passes `ANTHROPIC_API_KEY` (or the Bedrock / Vertex / Foundry switches) to Claude Code.
- Shadow's MCP server:
  - When you run Shadow **from source** (`run.bat`), the panel starts this repository's `tools/mcp_server.py` with the
    same Python.
  - The **installed Shadow.exe** does not contain the server. It uses the `shadow` server you registered for Claude Code
    (`claude mcp add shadow --scope user -- python C:\Shadow\tools\mcp_server.py`, see [MCP.md](MCP.md)). If none is
    registered, the panel tells you how to set it up.

## Using it
- **Enter** sends the message and **Shift+Enter** starts a new line. While Claude works, **Stop** ends the turn.
  Your next message continues the same conversation.
- **New chat** starts a fresh conversation.
- **Folder...** sets the working folder: Claude may read files there without asking. By default (*auto*) it is the
  folder of the selected scene, or else of the first scene in the queue, or else *Save to*. The folder is fixed when a
  conversation starts. Changing it starts a new conversation.
- Every message carries a short `[Shadow app]` block: the queue (paths, status, format, audio), *Save to*, the
  current settings and whether the window's own queue is rendering. That's how "this folder" or "these scenes" works.
- Claude replies in your language (Arabic is fine). Mixed Arabic + English lines can show their word groups in the wrong
  order, because Tk has no right-to-left layout. Pure Arabic sentences read correctly.

## Permissions: what Claude may do from the panel
Measured with Claude Code 2.1.327. The command line is in `assistant.build_args()`:

| Flag | Effect |
|------|--------|
| `--permission-mode dontAsk` | Anything not pre-approved is **denied** at once. Nothing waits for an answer nobody can give. |
| `--tools Read,Glob,Grep` | The only built-in tools are for reading. There is **no** Bash / PowerShell, no Edit / Write, and no web access. |
| `--strict-mcp-config --mcp-config <file>` | Only Shadow's MCP server is loaded. Your other MCP servers and plugin servers are not. |
| `--allowedTools shadow_info, shadow_plan, shadow_render, shadow_progress, shadow_jobs, shadow_check_frames, shadow_qc, shadow_outputs` | These tools run without asking. They only read, or they make new files: a render never overwrites (`_2`, `_3`...). |
| (not allowed) `shadow_cancel` | Stops a render, so it is **asked**. When Claude tries it, the panel shows *Claude wants to use shadow_cancel(...). Allow it once?* **Allow once** re-runs that one turn with the tool allowed. Afterwards it is denied again. **No** keeps it blocked. |
| `--add-dir <Save to>` | Claude may read your output folder as well (QC reports, logs). |

Reading a file outside those two folders is denied too, and the panel offers *Allow once* for it in the same way.
Nothing that runs code or changes files can be allowed from the panel.

## Lifetime of a render the assistant starts
One `claude` process holds the conversation (`--input-format stream-json`). Shadow's MCP server runs inside it, so a
render the assistant started keeps going between your messages. **Stop**, **New chat**, a folder change and closing
Shadow end that process, and with it a render *the assistant* started. The queue in the main window is a separate render
and is never touched. The assistant's renders use the same output folders, file names and QC as the main queue. Two
renders at once (the window's queue and the assistant's) work, but each one goes slower.

## Files
- `assistant.py`: finding the CLI, the sign-in check, the command line, the stream-json parser, the chat process (threads).
- `app.py`: the panel (`build_assistant`, `ai_*`). Settings `assistant_open` and `assistant_dir` are stored in `~/.shadow_renderer.json`.
- `%USERPROFILE%\.shadow_assistant\assistant_mcp.json`: the MCP config the panel passes to Claude Code.
- Tests: `python tests/test_assistant.py` (74 checks). It uses a fake CLI (`tests/fixtures/fake_claude.py`) that prints
  the event shapes recorded from the real CLI, so the tests never call Claude.

"""A stand-in for the Claude Code CLI, so the assistant tests never call Claude (tests/test_assistant.py).
Speaks the same stream-json lines the real `claude -p --input-format stream-json --output-format stream-json --verbose
--include-partial-messages` printed in a real run (Claude Code 2.1.327): system/init, stream_event text_delta, assistant
tool_use, system/permission_denied, rate_limit_event, result with permission_denials.
  claude auth status --json   FAKE_AUTH = ok (default) | none (exit 1, loggedIn false) | api_key
  claude -p ...               every command line is appended to FAKE_LOG (one JSON list per line); per message (last line):
      contains CANCEL (or 'Allow once', the panel's retry) -> tool_use shadow_cancel, denied unless --allowedTools names it
      contains SLOW   -> one delta, then waits 60 s (the Stop test kills it)
      contains CRASH  -> exits 3 with a stderr line
      contains LIMIT  -> rate_limit_event status=rejected
      otherwise       -> "Echo <n>: <last line>" in three deltas (n counts messages in this process)"""
import json, os, sys, time, uuid

def out(d):
    sys.stdout.write(json.dumps(d) + '\n'); sys.stdout.flush()

argv = sys.argv[1:]
if argv[:2] == ['auth', 'status']:
    m = os.environ.get('FAKE_AUTH', 'ok')
    if m == 'none':
        print(json.dumps({'loggedIn': False, 'authMethod': 'none'})); sys.exit(1)
    print(json.dumps({'loggedIn': True, 'authMethod': 'api_key' if m == 'api_key' else 'claude.ai', 'subscriptionType': 'pro'}))
    sys.exit(0)

if os.environ.get('FAKE_LOG'):
    with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as f:
        f.write(json.dumps({'argv': argv, 'cwd': os.getcwd(), 'api_key': 'ANTHROPIC_API_KEY' in os.environ}) + '\n')
sid = argv[argv.index('--resume') + 1] if '--resume' in argv else str(uuid.uuid4())
allowed = argv[argv.index('--allowedTools') + 1].split(',') if '--allowedTools' in argv else []
n = 0; first = True
for raw in sys.stdin:
    try: msg = json.loads(raw)['message']['content']
    except Exception: continue
    n += 1
    if first:
        out({'type': 'system', 'subtype': 'init', 'session_id': sid, 'cwd': os.getcwd(), 'tools': ['Read'],
             'mcp_servers': [{'name': 'shadow', 'status': 'connected'}], 'permissionMode': 'dontAsk'}); first = False
    last = msg.strip().splitlines()[-1]
    mid = f'msg_{n}'
    out({'type': 'stream_event', 'event': {'type': 'message_start', 'message': {'id': mid}}, 'parent_tool_use_id': None, 'session_id': sid})
    denials = []
    if 'CRASH' in last:
        sys.stderr.write('fake crash\n'); sys.stderr.flush(); sys.exit(3)
    if 'LIMIT' in last:
        out({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'rateLimitType': 'five_hour', 'resetsAt': 1795066800}})
    if 'CANCEL' in last or 'Allow once' in last:
        tool = 'mcp__shadow__shadow_cancel'
        out({'type': 'assistant', 'message': {'id': mid, 'content': [{'type': 'tool_use', 'id': 't1', 'name': tool, 'input': {'job_id': 'j1'}}]},
             'parent_tool_use_id': None, 'session_id': sid})
        if tool in allowed:
            out({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'cancelled'}]}})
            text = 'Cancelled j1.'
        else:
            out({'type': 'system', 'subtype': 'permission_denied', 'tool_name': tool, 'tool_use_id': 't1', 'tool_input': {'job_id': 'j1'}})
            denials.append({'tool_name': tool, 'tool_use_id': 't1', 'tool_input': {'job_id': 'j1'}})
            text = 'I could not cancel: blocked.'
        mid = f'msg_{n}b'
        out({'type': 'stream_event', 'event': {'type': 'message_start', 'message': {'id': mid}}, 'parent_tool_use_id': None})
    elif 'SLOW' in last:
        out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Working on it'}}, 'parent_tool_use_id': None})
        time.sleep(60); text = 'late'
    else:
        text = f'Echo {n}: {last}'
    for i in range(0, len(text), max(1, len(text) // 3)):
        out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text[i:i + max(1, len(text) // 3)]}},
             'parent_tool_use_id': None, 'session_id': sid})
    out({'type': 'assistant', 'message': {'id': mid, 'content': [{'type': 'text', 'text': text}]}, 'parent_tool_use_id': None, 'session_id': sid})
    out({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text, 'session_id': sid, 'num_turns': 1,
         'permission_denials': denials})

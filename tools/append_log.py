#!/usr/bin/env python3
from __future__ import annotations
import argparse, re
from datetime import datetime, timezone
from pathlib import Path

ISOZ = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')

def now_utc(): return datetime.now(timezone.utc)

def ts_utc(dt):
    if dt.tzinfo is None: raise ValueError('timestamp invalid: naive datetime')
    s = dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ');
    if not ISOZ.fullmatch(s): raise ValueError(f'timestamp invalid: {s!r}')
    return s

def value(name, s):
    s = (s or '').strip();
    if not s or s.lower() in {'unknown', 'auto', 'none', 'n/a', 'tool', 'model'}: raise ValueError(f'{name} invalid: {s!r}')
    return s

def main():
    a = argparse.ArgumentParser(); a.add_argument('--title', required=True); a.add_argument('--prompt-file'); a.add_argument('--prompt'); a.add_argument('--summary', required=True); a.add_argument('--actions', nargs='*', default=[]); a.add_argument('--tool', required=True); a.add_argument('--model', required=True); a.add_argument('--log', default=str(Path(__file__).resolve().parents[1] / 'log.txt'))
    args = a.parse_args(); ts = ts_utc(now_utc())
    prompt = Path(args.prompt_file).read_text(encoding='utf-8') if args.prompt_file else (args.prompt or '');
    if prompt.startswith(('<environment_info>', '<workspace_info>', '<conversation-summary>')): raise ValueError('prompt starts with forbidden marker')
    entry = '\n'.join(['## ' + ts + ' ' + args.title, '', 'User Prompt (verbatim, secrets redacted):', prompt, '', 'Agent Response Summary:', args.summary, '', 'Actions:', *[f'* {x}' for x in args.actions], '', 'Context:', f'tool={value("tool", args.tool)}', f'model={value("model", args.model)}']) + '\n'
    p = Path(args.log); p.open('a', encoding='utf-8', newline='\n').write(entry + '\n');
    t = p.read_text(encoding='utf-8'); b = t.split('## ')[-1]; tool_lines = [l for l in b.splitlines() if l.startswith('tool=') and l.strip() not in {'tool='}]; model_lines = [l for l in b.splitlines() if l.startswith('model=') and l.strip() not in {'model='}];
    if len(tool_lines) != 1 or len(model_lines) != 1: raise AssertionError('must have exactly one populated tool= and one populated model= in the new entry');
    print(entry)

if __name__ == '__main__': main()

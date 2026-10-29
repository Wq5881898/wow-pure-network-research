"""Read-only OpenCode session discovery; private exports never belong in Git."""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = Path.home() / '.local/share/opencode/opencode.db'


def main():
    connection = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print('tables:', tables)
    for table in ('session', 'message', 'part'):
        print(table, [r[1] for r in connection.execute(f'PRAGMA table_info({table})')])
    sessions = [dict(r) for r in connection.execute('SELECT * FROM session')
                if 'WoW' in str(dict(r).get('directory', ''))
                or '预研环境对接' in str(dict(r).get('title', ''))]
    out = ROOT / '.cache' / 'incident_private'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'sessions.json').write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding='utf-8')
    for row in sessions:
        print(json.dumps({k: row.get(k) for k in ('id', 'title', 'time_created', 'time_updated')}, ensure_ascii=True))
        sid = row['id']
        parts = [dict(r) for r in connection.execute('SELECT * FROM part WHERE session_id=? ORDER BY time_created', (sid,))]
        (out / f'{sid}.json').write_text(json.dumps(parts, ensure_ascii=False, indent=2), encoding='utf-8')
    connection.close()


if __name__ == '__main__':
    main()

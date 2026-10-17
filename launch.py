"""Local PC launcher: loopback only, automatic backup, isolated practice DB."""
import sys
if sys.version_info < (3, 12):
    raise SystemExit('Python 3.12 or newer is required. Install from https://www.python.org/downloads/')
from contextlib import closing
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
import webbrowser

ROOT = Path(__file__).resolve().parent


def backup_database(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Database integrity check failed')
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def prepare_database(root, practice=False):
    original = root / '.data' / 'research.sqlite3'
    selected = root / '.data' / ('practice.sqlite3' if practice else 'research.sqlite3')
    if practice and not selected.exists() and original.exists():
        backup_database(original, selected)
    if selected.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup_database(selected, root / '.backups' / f'{selected.stem}-{stamp}.sqlite3')
    return selected


def main():
    parser = argparse.ArgumentParser(description='Start the private PC research app.')
    parser.add_argument('--practice', action='store_true', help='Use a separate persistent practice database')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0, help='0 chooses an available local port')
    args = parser.parse_args()
    from server import create_server
    try:
        path = prepare_database(ROOT, args.practice)
        server = create_server(path, args.port, usage_mode='practice' if args.practice else 'personal')
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f'Could not start: {exc}\nCheck folder write permission, disk space, and port availability.', file=sys.stderr)
        return 1
    url = f'http://127.0.0.1:{server.server_port}/#start'
    print(f'Mode: {"PRACTICE (separate database)" if args.practice else "PERSONAL"}', flush=True)
    print(f'Data: {path}\nBackups: {ROOT / ".backups"}', flush=True)
    print(f'Open on THIS PC: {url}\nKeep this window open. Press Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        timer = threading.Timer(.5, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped. Saved records remain on disk.', flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

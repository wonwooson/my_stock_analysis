"""Package source and a consistent personal DB snapshot; omit secrets/practice/backups."""
import argparse
from pathlib import Path
import sys
import tempfile
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launch import backup_database

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    top_files = {'README.md', 'AGENTS.md', 'PC-시작안내.html', 'Start-Windows.bat',
                 'Practice-Windows.bat', 'Start-Mac.command', 'Practice-Mac.command', 'start-linux.sh'}
    folders = {'static', 'scripts', 'tests', 'docs'}
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / 'research.sqlite3'
        source = ROOT / '.data' / 'research.sqlite3'
        if source.exists():
            backup_database(source, snapshot)
        with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(ROOT.rglob('*')):
                rel = path.relative_to(ROOT)
                if not path.is_file() or any(part.startswith('.') or part == '__pycache__' for part in rel.parts):
                    continue
                if (len(rel.parts) == 1 and (path.suffix == '.py' or path.name in top_files)) or rel.parts[0] in folders:
                    archive.write(path, str(Path('my_stock_analysis') / rel))
            if snapshot.exists():
                archive.write(snapshot, 'my_stock_analysis/.data/research.sqlite3')
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip() is not None:
                raise ValueError('ZIP integrity check failed')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'my_stock_analysis-PC.zip')
    print(build(parser.parse_args().output))

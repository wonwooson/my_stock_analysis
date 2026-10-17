"""Run the complete local suite; missing browser coverage is a failure."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ['PYTHONUTF8'] = '1'
    node = shutil.which('node')
    if not node:
        print('Node.js is required for JavaScript syntax validation.', file=sys.stderr)
        return 1
    subprocess.run([node, '--check', 'static/app.js'], check=True)
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print('Full verification requires browser tests; skipped tests are not accepted.')
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())

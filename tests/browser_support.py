"""Use managed Chromium by default; allow an explicit system browser."""
import os
from pathlib import Path

local_browsers = Path(__file__).resolve().parents[1] / '.tools' / 'ms-playwright'
if local_browsers.exists():
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(local_browsers))


def launch_browser(playwright):
    executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
    options = {'executable_path': executable} if executable else {}
    return playwright.chromium.launch(headless=True, **options)

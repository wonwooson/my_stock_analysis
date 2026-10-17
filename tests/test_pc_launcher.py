from contextlib import closing
import os
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from launch import prepare_database
from server import ROOT, init_db, connect, append, rows


class PCLauncherTests(unittest.TestCase):
    def test_practice_isolation_backup_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);personal=root/'.data/research.sqlite3';init_db(personal)
            practice=prepare_database(root,True)
            with connect(practice) as db:append(db,'note','practice-test',{'demo':False,'answer':'practice only'},0)
            prepare_database(root,True)
            with connect(personal) as db:self.assertFalse(any(r['id']=='practice-test' for r in rows(db)))
            with connect(practice) as db:self.assertTrue(any(r['id']=='practice-test' for r in rows(db)))
            backups=list((root/'.backups').glob('*.sqlite3'))
            self.assertEqual(len(backups),2)
            for path in backups:
                with closing(sqlite3.connect(path)) as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_launcher_from_path_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory(prefix='market PC ') as directory:
            root=Path(directory)/'주식 앱';root.mkdir()
            for path in ROOT.glob('*.py'):shutil.copy(path,root/path.name)
            shutil.copytree(ROOT/'static',root/'static')
            init_db(root/'.data/research.sqlite3')
            log=root/'startup.log'
            with log.open('w', encoding='utf-8') as output:
                process=subprocess.Popen([sys.executable,str(root/'launch.py'),'--practice','--no-browser'],stdout=output,stderr=output,env={**os.environ, 'PYTHONUTF8': '1'})
                try:
                    import time,re
                    url=None
                    for _ in range(100):
                        match=re.search(r'http://127\.0\.0\.1:\d+',log.read_text(encoding='utf-8'))
                        if match:url=match.group();break
                        if process.poll() is not None:break
                        time.sleep(.05)
                    self.assertIsNotNone(url,log.read_text(encoding='utf-8'))
                    with urllib.request.urlopen(url+'/api/state') as response:state=json.load(response)
                    self.assertEqual(state['usage_mode'],'practice')
                    self.assertTrue((root/'.data/practice.sqlite3').exists())
                    with urllib.request.urlopen(url+'/api/health') as response:self.assertEqual(response.status,200)
                finally:
                    process.terminate();process.wait(timeout=10)

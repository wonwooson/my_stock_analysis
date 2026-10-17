import sqlite3
import tempfile
import unittest
from pathlib import Path
from server import connect


class ConnectionTests(unittest.TestCase):
    def test_commit_rollback_and_close(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'transaction.sqlite3'
            with connect(path) as db:
                db.execute('CREATE TABLE sample (value INTEGER)')
                db.execute('INSERT INTO sample VALUES (1)')
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')
            with self.assertRaisesRegex(ValueError, 'rollback'):
                with connect(path) as failed:
                    failed.execute('INSERT INTO sample VALUES (2)')
                    raise ValueError('rollback')
            with self.assertRaises(sqlite3.ProgrammingError):
                failed.execute('SELECT 1')
            with connect(path) as check:
                self.assertEqual([r[0] for r in check.execute('SELECT value FROM sample')], [1])
            path.unlink()  # Windows refuses this if a connection remains open.

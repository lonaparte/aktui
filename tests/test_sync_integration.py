"""Official sync-server tests with disposable accounts on loopback."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from anki.errors import SyncError
from aktui.storage import Profile
from aktui.core import Service


class SyncIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cls.endpoint = f'http://127.0.0.1:{port}/'
        env = dict(os.environ, SYNC_USER1='test:password', SYNC_USER2='text:password', SYNC_HOST='127.0.0.1',
                   SYNC_PORT=str(port), SYNC_BASE=str(cls.root / 'server'))
        cls.log = (cls.root / 'server.log').open('w+')
        cls.server = subprocess.Popen([sys.executable, '-m', 'anki.syncserver'], env=env, stdout=cls.log, stderr=cls.log)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1):
                    return
            except OSError:
                if cls.server.poll() is not None:
                    break
                time.sleep(.1)
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.log.seek(0)
        raise RuntimeError('Test server did not start: ' + cls.log.read())

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.log.close()
        cls.temp.cleanup()

    def test_login_full_incremental_reviews_media_and_recovery(self):
        profiles = [Profile(self.root, name) for name in ('a', 'b')]
        a, b = services = [Service(p) for p in profiles]
        try:
            for s in services:
                # Route only this test's login to the local official server.
                original = s.col.sync_login
                with patch.object(s.col, 'sync_login', side_effect=lambda **kw: original(**(kw | {'endpoint': self.endpoint}))):
                    with self.assertRaises(SyncError):
                        s.login('test', 'wrong-password')
                    s.login('test', 'password')
                s.profile.set_media(True)
            did = a.col.decks.id('Demo')
            n = a.col.new_note(a.col.models.by_name('Basic'))
            n['Front'], n['Back'] = 'front', 'back'
            a.col.add_note(n, did)
            a.col.media.write_data('hello.txt', b'hello media')
            a.sync()
            self.assertIsNotNone(a.pending)
            a.full_sync('upload')
            b.sync()
            self.assertIsNotNone(b.pending)
            b.full_sync('download')
            self.assertEqual(b.col.get_note(n.id)['Back'], 'back')
            self.assertEqual((Path(b.col.media.dir()) / 'hello.txt').read_bytes(), b'hello media')
            note = b.col.get_note(n.id)
            note['Back'] = 'updated'
            b.col.update_note(note)
            b.select_deck(did)
            card, states = b.next_card()
            b.answer(card, states, 3)
            b.sync()
            a.sync()
            self.assertEqual(a.col.get_note(n.id)['Back'], 'updated')
            self.assertEqual(a.col.db.scalar('select count(*) from revlog'), 1)
            # Repeated no-change syncs must not consume backup versions.
            before = set((a.profile.path / 'backups').glob('*.colpkg'))
            a.sync()
            self.assertEqual(set((a.profile.path / 'backups').glob('*.colpkg')), before)
            # A transport failure must retain a usable local collection.
            a.pending = type('Pending', (), {'required': 2})()
            with patch.object(a.col, 'full_upload_or_download', side_effect=OSError('offline')):
                with self.assertRaises(OSError):
                    a.full_sync('download')
            self.assertEqual(a.col.get_note(n.id)['Back'], 'updated')
            self.assertIsNone(a.pending)
        finally:
            for s in services:
                s.close()
            for p in profiles:
                p.close()

    def test_text_only_roundtrip_preserves_fields_templates_and_remote_media(self):
        profiles = [Profile(self.root, name) for name in ('source', 'text', 'check')]
        a, b, c = services = [Service(p) for p in profiles]
        try:
            for s in services:
                original = s.col.sync_login
                with patch.object(s.col, 'sync_login', side_effect=lambda **kw: original(**(kw | {'endpoint': self.endpoint}))):
                    s.login('text', 'password')

            model = a.col.models.new('Three fields')
            for name in ('Front', 'Back', 'Extra'):
                a.col.models.add_field(model, a.col.models.new_field(name))
            template = a.col.models.new_template('Card')
            template['qfmt'] = '{{Front}}'
            template['afmt'] = '{{FrontSide}}<hr>{{Back}}<br>{{Extra}}'
            a.col.models.add_template(model, template)
            a.col.models.add(model)
            did = a.col.decks.id('Text::\u4e2d\u6587')
            n = a.col.new_note(model)
            n['Front'] = '\u4e2d\u6587 &amp; <b>question</b>'
            n['Back'] = 'answer<br>second line'
            n['Extra'] = '<img src="keep.txt">[sound:keep.txt]'
            n.tags = ['\u4e2d\u6587', 'tag']
            a.col.add_note(n, did)
            a.col.media.write_data('keep.txt', b'media must remain on server')

            # Default-off full upload must not upload the local media file.
            a.sync()
            a.full_sync('upload')
            c.profile.set_media(True)
            c.sync()
            c.full_sync('download')
            self.assertFalse((Path(c.col.media.dir()) / 'keep.txt').exists())

            # Explicit opt-in works even with no collection changes.
            a.profile.set_media(True)
            a.sync()

            # All text-only operations must avoid both starting and polling media.
            with patch.object(b.col, 'sync_media', side_effect=AssertionError('media started')), patch.object(b.col, 'media_sync_status', side_effect=AssertionError('media polled')):
                b.sync()
                b.full_sync('download')
                got = b.col.get_note(n.id)
                self.assertEqual(got.fields, n.fields)
                self.assertEqual(set(got.tags), set(n.tags))
                self.assertEqual(b.col.models.get(model['id'])['tmpls'], a.col.models.get(model['id'])['tmpls'])
                self.assertFalse((Path(b.col.media.dir()) / 'keep.txt').exists())
                got['Back'] = '\u7eaf\u6587\u672c\u7f16\u8f91 &amp; preserved HTML'
                b.col.update_note(got)
                b.select_deck(did)
                card, states = b.next_card()
                b.answer(card, states, 3)
                b.sync()

            # Verify remote media survives text-only edits and reviews.
            c.sync()
            self.assertEqual((Path(c.col.media.dir()) / 'keep.txt').read_bytes(), b'media must remain on server')
            self.assertEqual(c.col.get_note(n.id)['Back'], got['Back'])
            self.assertEqual(c.col.get_note(n.id)['Extra'], n['Extra'])
            self.assertEqual(c.col.db.scalar('select count(*) from revlog'), 1)
            self.assertFalse(b.col.media_sync_status().active)
        finally:
            for s in services:
                s.close()
            for p in profiles:
                p.close()


if __name__ == '__main__':
    unittest.main()

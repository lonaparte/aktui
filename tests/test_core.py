import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from anki.errors import SyncError, SyncErrorKind
from anki.sync_pb2 import SyncCollectionResponse as Result, SyncAuth
from aktui.core import Service
from aktui.storage import Profile
from aktui.render import plain


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile = Profile(self.root, 'test')
        self.s = Service(self.profile)
        self.did = self.s.col.decks.id('Language::\u4e2d\u6587')

    def tearDown(self):
        self.s.close()
        self.profile.close()
        self.temp.cleanup()

    def add(self, front='\u4e2d\u6587', back='answer'):
        n = self.s.col.new_note(self.s.col.models.by_name('Basic'))
        n['Front'], n['Back'] = front, back
        self.s.col.add_note(n, self.did)
        return n

    def test_review_persisted(self):
        n = self.add()
        self.s.select_deck(self.did)
        card, states = self.s.next_card()
        self.assertIn('\u4e2d\u6587', card.question())
        self.s.answer(card, states, 3)
        self.assertEqual(self.s.col.db.scalar('select count(*) from revlog'), 1)
        self.s.close()
        self.s = Service(self.profile)
        self.assertEqual(self.s.col.get_note(n.id)['Back'], 'answer')
        self.assertEqual(self.s.col.db.scalar('select count(*) from revlog'), 1)

    def test_edit(self):
        n = self.add()
        n['Back'] = 'changed <b>\u7b54\u6848</b>'
        n.tags = ['\u4e2d\u6587', 'tag']
        self.s.col.update_note(n)
        got = self.s.col.get_note(n.id)
        self.assertEqual(got['Back'], n['Back'])
        self.assertEqual(set(got.tags), set(n.tags))

    def test_keyset_pagination_child_decks_and_literal_search(self):
        for i in range(65):
            self.add(f'item {i} %_')
        parent = self.s.col.decks.id('Language')
        first = self.s.note_page(parent)
        second = self.s.note_page(parent, after=first[-1][0])
        third = self.s.note_page(parent, after=second[-1][0])
        self.assertEqual(list(map(len, (first, second, third))), [30, 30, 5])
        self.assertEqual(len({row[0] for page in (first, second, third) for row in page}), 65)
        self.assertEqual(len(self.s.note_page(parent, query='%_', limit=100)), 65)
        self.assertEqual(self.s.note_page(parent, query="' OR 1=1 --"), [])
        self.assertEqual(self.s.note_page(self.s.col.decks.id('Other')), [])

    def test_lock(self):
        with self.assertRaises(RuntimeError):
            Profile(self.root, 'test')

    def test_traversal(self):
        for name in ('../bad', '', '/tmp', 'a/b'):
            with self.assertRaises(ValueError):
                Profile(self.root, name)

    def test_credentials_and_binding(self):
        auth = SyncAuth(hkey='test-token', endpoint='https://example.invalid/')
        with patch.object(self.s.col, 'sync_login', return_value=auth):
            self.s.login('user@example.invalid', 'never-store-me')
        saved = self.profile.auth_path.read_text()
        self.assertNotIn('never-store-me', saved)
        self.assertEqual(stat.S_IMODE(self.profile.auth_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.profile.path.stat().st_mode), 0o700)
        self.profile.logout()
        self.assertNotIn('test-token', self.profile.auth_path.read_text())
        with self.assertRaises(ValueError):
            self.s.login('different@example.invalid', 'x')

    def test_backup(self):
        self.add()
        self.s.backup()
        self.assertTrue(list((self.profile.path / 'backups').glob('*.colpkg')))

    def test_full_sync_requires_choice_and_backup(self):
        self.profile.save_account({'username': 'a', 'hkey': 'b'})
        result = Result(required=Result.FULL_DOWNLOAD, new_endpoint='https://example.invalid/')
        with patch.object(self.s.col, 'sync_collection', return_value=result), patch.object(self.s.col, 'full_upload_or_download') as full:
            self.s.sync()
            full.assert_not_called()
            self.assertEqual(self.s.auth().endpoint, 'https://example.invalid/')
            with self.assertRaises(ValueError):
                self.s.full_sync('upload')
            with patch.object(self.s, 'backup', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    self.s.full_sync('download')
            full.assert_not_called()
            self.assertIsNotNone(self.s.col.db)

    def test_media_default_off(self):
        self.profile.save_account({'hkey': 'b'})
        with patch.object(self.s.col, 'sync_collection', return_value=Result(required=Result.NO_CHANGES)), patch.object(self.s.col, 'sync_media') as media:
            self.s.sync()
            media.assert_not_called()

    def test_sync_without_login_does_not_create_backup(self):
        with self.assertRaisesRegex(ValueError, 'Log in'):
            self.s.sync()
        self.assertFalse((self.profile.path / 'backups').exists())

    def test_media_failure_is_reported_and_auth_expiry_keeps_binding(self):
        self.profile.save_account({'username': 'user', 'hkey': 'expired'})
        self.profile.set_media(True)
        error = SyncError('expired', None, None, None, SyncErrorKind.AUTH)
        with patch.object(self.s.col, 'sync_media'), patch.object(self.s.col, 'media_sync_status', side_effect=error):
            with self.assertRaises(SyncError):
                self.s.sync_media()
        self.assertEqual(self.profile.account(), {'username': 'user'})

        self.profile.save_account({'username': 'user', 'hkey': 'valid'})
        with patch.object(self.s.col, 'sync_media'), patch.object(self.s.col, 'media_sync_status', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                self.s.sync_media()
        self.assertEqual(self.profile.account()['hkey'], 'valid')

    def test_text_rendering(self):
        text = plain('<style>hide</style><script>danger()</script><p>\u4f60\u597d &amp; hi</p><img src="x.png"><br>[sound:x.mp3]\x1b[31m')
        self.assertIn('\u4f60\u597d & hi', text)
        self.assertIn('[image: x.png]', text)
        self.assertIn('[audio: x.mp3]', text)
        self.assertNotIn('hide', text)
        self.assertNotIn('danger', text)
        self.assertNotIn('\x1b', text)


if __name__ == '__main__':
    unittest.main()

"""Verify content-based retention and recoverable backup packages."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anki._backend import RustBackend
from anki.collection import Collection, media_paths_from_col_path
from anki.decks_pb2 import Deck

from aktui.backups import INDEX, content_fingerprint
from aktui.core import Service
from aktui.storage import Profile


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = Profile(Path(self.temp.name), 'test')
        self.service = Service(self.profile)
        self.col = self.service.col
        self.did = self.col.decks.id('Study')
        self.note = self.col.new_note(self.col.models.by_name('Basic'))
        self.note['Front'], self.note['Back'] = 'question', 'version A'
        self.note.tags = ['test']
        self.col.add_note(self.note, self.did)
        self.folder = self.profile.path / 'backups'

    def tearDown(self):
        self.service.close()
        self.profile.close()
        self.temp.cleanup()

    def change(self, value):
        self.note['Back'] = value
        self.col.update_note(self.note)

    def packages(self):
        return set(self.folder.glob('*.colpkg'))

    def restored_answer(self, package):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'collection.anki2'
            media_dir, media_db = media_paths_from_col_path(str(path))
            RustBackend().import_collection_package(
                col_path=str(path), backup_path=str(package),
                media_folder=media_dir, media_db=media_db,
            )
            restored = Collection(str(path))
            try:
                return restored.get_note(self.note.id)['Back']
            finally:
                restored.close()

    def test_unchanged_content_does_not_add_or_replace_a_backup(self):
        first = self.service.backup()
        before = first.stat()
        for _ in range(4):
            self.assertEqual(self.service.backup(), first)
        self.assertEqual(self.packages(), {first})
        self.assertEqual(first.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(first.stat().st_ino, before.st_ino)

    def test_sync_timestamps_selection_and_daily_counters_do_not_rotate(self):
        first = self.service.backup()
        for table in ('col', 'cards', 'notes', 'decks', 'deck_config', 'notetypes', 'templates', 'config', 'tags'):
            self.col.db.execute(f'update {table} set usn = usn + 5')
        self.col.db.execute('update col set mod = mod + 1000, ls = ls + 1000, scm = scm + 1000')
        self.col.db.execute('update notes set mod = mod + 1')
        self.col.db.execute('update cards set mod = mod + 1')
        self.col.db.execute('update config set mtime_secs = mtime_secs + 1000')
        self.col.decks.select(1)
        self.col.set_config('lastUnburied', 123)
        common = Deck.Common()
        common.ParseFromString(bytes(self.col.db.scalar('select common from decks where id = ?', self.did)))
        common.last_day_studied += 1
        common.new_studied += 1
        common.study_collapsed = not common.study_collapsed
        self.col.db.execute('update decks set common = ? where id = ?', list(common.SerializeToString()), self.did)
        self.col.db.execute('update tags set collapsed = 1')
        self.assertEqual(self.service.backup(), first)
        self.assertEqual(self.packages(), {first})

    def test_only_three_distinct_versions_remain_and_can_be_restored(self):
        for version in ('A', 'B', 'C', 'D', 'E'):
            self.change('version ' + version)
            self.service.backup()
        self.assertEqual(len(self.packages()), 3)
        self.assertEqual({self.restored_answer(path) for path in self.packages()}, {'version C', 'version D', 'version E'})

    def test_reverting_content_does_not_keep_duplicate_versions(self):
        for version in ('A', 'B', 'C', 'A'):
            self.change('version ' + version)
            latest = self.service.backup()
        self.assertEqual(len(self.packages()), 3)
        self.assertEqual(self.restored_answer(latest), 'version A')
        self.assertEqual({self.restored_answer(path) for path in self.packages()}, {'version A', 'version B', 'version C'})

    def test_content_changes_are_detected_without_new_modification_timestamps(self):
        first = self.service.backup()
        old_mod = self.note.mod
        self.change('modified with original timestamp')
        self.col.db.execute('update notes set mod = ? where id = ?', old_mod, self.note.id)
        second = self.service.backup()
        self.assertNotEqual(first, second)
        self.assertEqual(self.restored_answer(second), 'modified with original timestamp')

    def test_review_progress_and_preferences_are_content_changes(self):
        first = self.service.backup()
        self.service.select_deck(self.did)
        card, states = self.service.next_card()
        self.service.answer(card, states, 3)
        second = self.service.backup()
        self.assertNotEqual(second, first)
        self.col.set_config('dueCounts', False)
        third = self.service.backup()
        self.assertNotEqual(third, second)
        self.assertEqual(len(self.packages()), 3)

    def test_failed_export_keeps_previous_versions_and_reopens_collection(self):
        for version in ('A', 'B', 'C'):
            self.change('version ' + version)
            self.service.backup()
        before = self.packages()
        self.change('version D')
        with patch.object(self.col._backend, 'export_collection_package', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.service.backup()
        self.assertEqual(self.packages(), before)
        self.assertIsNotNone(self.col.db)
        self.assertEqual(self.col.get_note(self.note.id)['Back'], 'version D')

    def test_legacy_backups_are_deduplicated_without_losing_distinct_versions(self):
        self.folder.mkdir()
        for index, version in enumerate(('A', 'A', 'B')):
            self.change('version ' + version)
            self.col.export_collection_package(str(self.folder / f'legacy-{index}.colpkg'), include_media=False, legacy=False)
            self.col.reopen()
        latest = self.service.backup()
        self.assertEqual(latest.name, 'legacy-2.colpkg')
        self.assertEqual(len(self.packages()), 2)
        self.assertEqual({self.restored_answer(path) for path in self.packages()}, {'version A', 'version B'})

    def test_missing_backup_or_invalid_index_is_repaired(self):
        latest = self.service.backup()
        (self.folder / INDEX).write_text('invalid JSON', encoding='utf-8')
        self.assertEqual(self.service.backup(), latest)
        latest.unlink()
        replacement = self.service.backup()
        self.assertTrue(replacement.is_file())
        saved = json.loads((self.folder / INDEX).read_text(encoding='utf-8'))
        self.assertEqual(saved['backups'][0]['content'], content_fingerprint(self.col))


if __name__ == '__main__':
    unittest.main()

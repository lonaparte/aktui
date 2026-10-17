"""Synthetic, media-free benchmark. Creates only disposable data."""
import argparse
import json
import os
from pathlib import Path
import resource
import tempfile
import time

from anki.collection import AddNoteRequest
from aktui.storage import Profile
from aktui.core import Service

parser = argparse.ArgumentParser()
parser.add_argument('--notes', type=int, default=10000)
args = parser.parse_args()
start = time.monotonic()
with tempfile.TemporaryDirectory() as root:
    p = Profile(Path(root), 'bench')
    s = Service(p)
    did = s.col.decks.id('Benchmark')
    model = s.col.models.by_name('Basic')
    for first in range(0, args.notes, 100):
        batch = []
        for i in range(first, min(first + 100, args.notes)):
            note = s.col.new_note(model)
            note['Front'] = f'Question {i}'
            note['Back'] = f'Answer {i}'
            batch.append(AddNoteRequest(note=note, deck_id=did))
        s.col.add_notes(batch)
    s.select_deck(did)
    item = s.next_card()
    if item:
        s.answer(*item, rating=3)
    page = s.note_page(did)
    s.backup()
    s.close()
    sizes = {f.name: f.stat().st_size for f in p.path.rglob('*') if f.is_file()}
    print(json.dumps({'notes': args.notes, 'page_rows': len(page),
                      'elapsed_seconds': round(time.monotonic() - start, 3),
                      'peak_rss_mib_linux': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
                      'profile_disk_mib': round(sum(sizes.values()) / 1024**2, 3),
                      'files_bytes': sizes}, indent=2))
    p.close()

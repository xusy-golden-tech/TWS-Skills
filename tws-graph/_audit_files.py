"""Audit file extensions in g-ass-source — find what's not indexed."""
import os
from collections import Counter
import sqlite3

root = 'D:/g-ass-source'

# Get indexed languages
conn = sqlite3.connect('D:/g-ass-source/.tws/codegraph/index.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT DISTINCT language FROM files').fetchall()
print('Indexed languages:')
for r in rows:
    print(f'  {r["language"]}')

# Scan
from tws_graph.indexer.scanner import scan_directory
scanned = set(scan_directory(root))

# Check what's not scanned
not_scanned_exts = Counter()
for dirpath, dirnames, filnames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in (
        '.git', 'node_modules', '__pycache__', '.venv', 'venv',
        'dist', 'build', '.next', '.tws')]
    for f in filnames:
        rel = os.path.relpath(os.path.join(dirpath, f), root)
        rel = rel.replace('\\', '/')
        ext = os.path.splitext(f)[1].lower()
        if rel not in scanned:
            not_scanned_exts[ext] += 1

print(f'\nScanned: {len(scanned)} files')
print(f'Not scanned: {sum(not_scanned_exts.values())} files')
print('\nTop not-scanned extensions (potential to add):')
for ext, cnt in not_scanned_exts.most_common(30):
    print(f'  {ext or "(no ext)":20s} {cnt:>6d}')

# Check which are source code that could be indexed
source_exts = {'.sh', '.bash', '.lua', '.c', '.cpp', '.h', '.hpp', '.f90', '.f',
               '.pyx', '.pxd', '.pxi', '.rego', '.mjs', '.csh', '.fish', '.ps1',
               '.ini', '.cfg', '.toml', '.rst'}
print('\nPotentially indexable source files not currently indexed:')
for ext, cnt in not_scanned_exts.most_common(60):
    if ext in source_exts:
        print(f'  {ext:20s} {cnt:>6d}')

conn.close()

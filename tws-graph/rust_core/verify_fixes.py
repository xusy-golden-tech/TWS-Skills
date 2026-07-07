"""End-to-end verification of all bug fixes."""
import os
import sys

# Ensure PATH is set
print("=== Verify BUG #1 (index no crash) ===")
from _core._core import index, search, calls, lint_skills

# Test index
db_path = r"C:\Temp\verify_fixes.db"
src_dir = r"D:\TWS-Skills\tws-graph\rust_core\src"

result = index(db_path, src_dir)
print(f"  Index: {result}")

# Test search
print("\n=== Verify BUG #2 (FTS5 search) ===")
results = search(db_path, "parse", 10)
print(f"  Search 'parse': {len(results)} results")
for r in results[:3]:
    print(f"    {r['name']} ({r['kind']}) @ {r['file_path']}")

# Test line_number
print("\n=== Verify BUG #5 (line_number) ===")
for r in results[:3]:
    print(f"    {r['name']} line={r.get('line_number', 'MISSING')}")

# Test calls
print("\n=== Verify BUG #3 (calls direction) ===")
call_result = calls(db_path, "parse_query", True, 2)
print(f"  Calls (inbound) for parse_query:")
print(call_result[:300] if len(call_result) > 300 else call_result)

# Test lint (file may not exist since we're not in skills dir)
print("\n=== Verify BUG #4 (lint format) ===")
lint_result = lint_skills(r"D:\TWS-Skills", False)
print(lint_result[:500])

print("\n=== ALL VERIFICATIONS COMPLETE ===")

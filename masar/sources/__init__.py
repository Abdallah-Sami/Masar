"""Job-source adapters. Each module exposes `run() -> int` (process exit code)."""
SOURCES = ("sabbar", "tanqeeb")

# Wuzzuf was dropped (Oct 2026): it blocks job pages from cloud servers and
# listed under 100 Saudi jobs. wuzzuf.py is kept, and its old data stays in
# the history; re-add it here and in daily.yml to turn it back on.

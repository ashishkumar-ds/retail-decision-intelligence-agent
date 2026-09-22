# Append-only JSONL files are the system of record

The recommendation log, approval ledger, execution journal and phase-2
registry are append-only JSONL files under `logs/`, not database tables. The
audit trail must be reconstructible by reading a file in order: malformed
lines are skipped and left in place, entries are fsynced under an exclusive
lock, and nothing is ever rewritten or deleted. SQLite exists for the derived
pending-approval *cache* only (`app/state.py`); the log remains the source of
truth from which that cache is rebuilt at startup.

## Considered Options

- Postgres for everything (rejected for now: the file trail is the audit
  artifact; a migration must keep `memory/history.py`'s append-only, fsynced
  contract and move with its golden cases in the same commit)
- SQLite as system of record (rejected: the cache role is separate from the
  evidence role; mixing them couples rebuild logic to storage)

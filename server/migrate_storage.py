#!/usr/bin/env python3
"""Convert a consistent legacy SQLite backup to private durable JSON. No GitHub writes."""
import argparse
from pathlib import Path
from receiver import migrate_database

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Consistent backup copied to local storage")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    counts = migrate_database(args.database, args.data_dir)
    print("Migrated", counts["sessions"], "sessions and", counts["groups"], "groups; no annotations published")

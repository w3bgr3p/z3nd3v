import os
import shutil
from pathlib import Path

# Source and destination paths
source_dir = Path("W:/code_hard/Parsers/hh/output")
dest_dir = Path("W:/code_hard/ai/jobs/raw-json")
dest_file = dest_dir / "hh.json"

# Find the latest JSON file in source directory
json_files = list(source_dir.glob("*.json"))
if not json_files:
    print("[ERROR] No JSON files found in hh output directory")
    exit(1)

# Get the most recent file
latest_file = max(json_files, key=lambda p: p.stat().st_mtime)

# Ensure destination directory exists
dest_dir.mkdir(parents=True, exist_ok=True)

# Copy and rename
shutil.copy2(latest_file, dest_file)
print(f"[OK] Copied {latest_file.name} -> {dest_file}")
print(f"[OK] File size: {dest_file.stat().st_size} bytes")

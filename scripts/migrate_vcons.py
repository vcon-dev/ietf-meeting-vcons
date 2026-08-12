#!/usr/bin/env python3
"""Migrate existing vCon files to be compliant with draft-ietf-vcon-vcon-core-02.

This script fixes the following compliance issues found by vcon-lib validation:

1. Dialog: `mimetype` → `mediatype` (spec field name)
2. Attachments: `type` → `purpose` (spec field name)
3. Analysis: add missing `encoding` field (required by spec)
4. Top-level: update `vcon` version from "0.0.1" to "0.0.1" (leave as-is,
   vcon-lib doesn't flag this)

Usage:
    python scripts/migrate_vcons.py [--dry-run] [--meeting 124]
    python scripts/migrate_vcons.py --validate  # validate only, no changes
"""

import argparse
import glob
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def fix_dialog(dialog: dict) -> list[str]:
    """Fix dialog object compliance issues. Returns list of changes made."""
    changes = []

    # Fix 1: mimetype → mediatype
    if "mimetype" in dialog and "mediatype" not in dialog:
        dialog["mediatype"] = dialog.pop("mimetype")
        changes.append("dialog: renamed 'mimetype' → 'mediatype'")

    return changes


def fix_attachment(attachment: dict, index: int) -> list[str]:
    """Fix attachment object compliance issues. Returns list of changes made."""
    changes = []

    # Fix 2: type → purpose
    if "type" in attachment and "purpose" not in attachment:
        attachment["purpose"] = attachment.pop("type")
        changes.append(f"attachment[{index}]: renamed 'type' → 'purpose'")

    return changes


def fix_analysis(analysis: dict, index: int) -> list[str]:
    """Fix analysis object compliance issues. Returns list of changes made."""
    changes = []

    # Fix 3: add missing encoding
    if "encoding" not in analysis:
        body = analysis.get("body")
        if isinstance(body, dict):
            analysis["encoding"] = "json"
            changes.append(f"analysis[{index}]: added encoding='json'")
        elif isinstance(body, str):
            analysis["encoding"] = "none"
            changes.append(f"analysis[{index}]: added encoding='none'")
        else:
            # Default to json for complex types
            analysis["encoding"] = "json"
            changes.append(f"analysis[{index}]: added encoding='json' (default)")

    return changes


def migrate_vcon(vcon_data: dict) -> list[str]:
    """Apply all compliance fixes to a vCon dict. Returns list of changes."""
    all_changes = []

    # Fix dialogs
    for i, dialog in enumerate(vcon_data.get("dialog", [])):
        changes = fix_dialog(dialog)
        all_changes.extend(changes)

    # Fix attachments
    for i, attachment in enumerate(vcon_data.get("attachments", [])):
        changes = fix_attachment(attachment, i)
        all_changes.extend(changes)

    # Fix analysis
    for i, analysis in enumerate(vcon_data.get("analysis", [])):
        changes = fix_analysis(analysis, i)
        all_changes.extend(changes)

    return all_changes


def validate_with_vcon_lib(file_path: str) -> tuple[bool, list[str]]:
    """Validate a vCon file using vcon-lib. Returns (is_valid, errors)."""
    try:
        # Try to import vcon-lib
        from vcon import Vcon
        return Vcon.validate_file(file_path)
    except ImportError:
        logger.warning("vcon-lib not available, skipping validation")
        return True, []


def process_file(file_path: Path, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Process a single vCon file. Returns (was_modified, changes)."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    changes = migrate_vcon(data)

    if changes and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    return bool(changes), changes


def main():
    parser = argparse.ArgumentParser(
        description="Migrate vCon files to be spec-compliant"
    )
    parser.add_argument(
        "--meeting",
        type=int,
        help="Only process a specific IETF meeting number",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without modifying files",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run vcon-lib validation after migration (or standalone)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate, don't migrate",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show per-file details",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Find base directory (repo root)
    script_dir = Path(__file__).parent
    base_dir = script_dir.parent

    # Find vCon files
    if args.meeting:
        pattern = base_dir / f"ietf{args.meeting}" / "*.vcon.json"
    else:
        pattern = base_dir / "ietf*" / "*.vcon.json"

    files = sorted(glob.glob(str(pattern)))
    if not files:
        print(f"No vCon files found matching {pattern}")
        sys.exit(1)

    print(f"Found {len(files)} vCon files\n")

    if args.validate_only:
        # Validation-only mode
        try:
            sys.path.insert(0, str(Path.home() / "Documents/GitHub/vcon-lib/src"))
            from vcon import Vcon
        except ImportError:
            print("ERROR: vcon-lib not available. Install it or check path.")
            sys.exit(1)

        valid_count = 0
        invalid_count = 0
        error_summary = {}

        for file_path in files:
            is_valid, errors = Vcon.validate_file(file_path)
            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1
                if args.verbose:
                    print(f"  INVALID: {Path(file_path).name}")
                    for e in errors:
                        print(f"    - {e}")
                for e in errors:
                    key = e.split(":")[0] if ":" in e else e
                    error_summary[key] = error_summary.get(key, 0) + 1

        print(f"Valid:   {valid_count}/{len(files)}")
        print(f"Invalid: {invalid_count}/{len(files)}")
        if error_summary:
            print("\nError summary:")
            for k, v in sorted(error_summary.items(), key=lambda x: -x[1]):
                print(f"  {v:4d}x  {k}")
        return

    # Migration mode
    modified_count = 0
    unchanged_count = 0
    change_summary = {}

    for file_path in files:
        was_modified, changes = process_file(Path(file_path), dry_run=args.dry_run)

        if was_modified:
            modified_count += 1
            if args.verbose:
                action = "Would modify" if args.dry_run else "Modified"
                print(f"  {action}: {Path(file_path).name}")
                for c in changes:
                    print(f"    - {c}")
            for c in changes:
                key = c.split(":")[0] if ":" in c else c
                change_summary[key] = change_summary.get(key, 0) + 1
        else:
            unchanged_count += 1

    action = "Would modify" if args.dry_run else "Modified"
    print(f"{action}: {modified_count}/{len(files)} files")
    print(f"Unchanged: {unchanged_count}/{len(files)} files")

    if change_summary:
        print("\nChanges by type:")
        for k, v in sorted(change_summary.items(), key=lambda x: -x[1]):
            print(f"  {v:4d}x  {k}")

    # Optional post-migration validation
    if args.validate and not args.dry_run:
        print("\n--- Post-migration validation ---")
        try:
            sys.path.insert(0, str(Path.home() / "Documents/GitHub/vcon-lib/src"))
            from vcon import Vcon

            valid_count = 0
            invalid_count = 0
            remaining_errors = {}

            for file_path in files:
                is_valid, errors = Vcon.validate_file(file_path)
                if is_valid:
                    valid_count += 1
                else:
                    invalid_count += 1
                    for e in errors:
                        key = e.split(":")[0] if ":" in e else e
                        remaining_errors[key] = remaining_errors.get(key, 0) + 1

            print(f"Valid:   {valid_count}/{len(files)}")
            print(f"Invalid: {invalid_count}/{len(files)}")
            if remaining_errors:
                print("\nRemaining errors:")
                for k, v in sorted(remaining_errors.items(), key=lambda x: -x[1]):
                    print(f"  {v:4d}x  {k}")
        except ImportError:
            print("vcon-lib not available, skipping validation")


if __name__ == "__main__":
    main()

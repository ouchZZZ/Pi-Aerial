"""Print a live, read-only Apple catalog inventory (no media downloads).

Run from the repository root: python -m tools.apple_inventory
"""
import json

from app import apple_catalog


def main():
    report = apple_catalog.inventory()
    stats = {key: report[key] for key in (
        "raw_counts", "counts", "skipped", "errors", "records", "unique", "merged")}
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
    tvos_only = [r for r in report["items"] if any(s.startswith("tvos") for s in r["sources"])]
    mac_only = [r for r in report["items"]
                if not any(s.startswith("tvos") for s in r["sources"])]
    print(f"Scenes with tvOS entry: {len(tvos_only)}")
    print(f"Scenes without tvOS entry: {len(mac_only)}")
    for row in mac_only:
        print(json.dumps({
            "name": row["name"], "name_en": row["name_en"],
            "ids": row["apple_ids"], "shot_ids": row["shot_ids"],
            "sources": row["sources"], "renditions": len(row["versions"]),
        }, ensure_ascii=False))
    if report["errors"]:
        print("Inventory is partial: one or more feeds could not be read.")
    return 0 if report["items"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

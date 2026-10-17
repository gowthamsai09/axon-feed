"""Summarise a feed output folder so tagging and ranking can be reviewed by eye.

python -m scripts.inspect_feed ..\\feed-out [--top 15]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(feed: Path, top: int = 15) -> str:
    meta = _load(feed / "meta.json")
    items = [_load(p) for p in sorted((feed / "items").glob("*.json"))]
    items.sort(key=lambda i: (-i["base_score"], i["id"]))
    out = [
        f"generated {meta['generated_at']}  window {meta['window_hours']:g}h",
        f"counts {meta['counts']}",
        "",
        "SOURCES",
    ]
    for s in meta["sources"]:
        out.append(
            f"  {s['status']:<7} {s['id']:<22} fetched={s['fetched']:<5} "
            f"collected={s['collected']:<5} in_window={s['in_window']}"
        )
    out += ["", f"WARNINGS ({len(meta['warnings'])})"] + [f"  {w}" for w in meta["warnings"][:20]]
    tag_counts = Counter(t for i in items for t in i["tags"])
    out += ["", "TAGS (items per topic)"]
    out += [f"  {n:>4}  {t}" for t, n in tag_counts.most_common()]
    no_tag = [i for i in items if not i["tags"]]
    out += ["", f"ITEMS WITHOUT ANY TAG: {len(no_tag)} of {len(items)}"]
    out += [f"  {i['source_id']:<18} {i['title'][:90]}" for i in no_tag[:8]]
    ent = Counter(e for i in items for e in i.get("entities", []))
    out += ["", "ENTITIES"] + [f"  {n:>4}  {e}" for e, n in ent.most_common(15)]
    by_src = Counter(i["source_id"] for i in items)
    out += ["", "ITEMS PER SOURCE (after merging)"] + [
        f"  {n:>4}  {s}" for s, n in by_src.most_common()
    ]
    merged = [i for i in items if i.get("also_covered_by")]
    out += ["", f"MERGED CLUSTERS: {len(merged)}"]
    out += [f"  covered by {len(i['also_covered_by'])} more: {i['title'][:80]}" for i in merged[:5]]
    out += ["", f"TOP {top} BY SCORE"]
    for i in items[:top]:
        tags = ",".join(i["tags"][:3])
        out.append(f"  {i['base_score']:.3f} {i['source_id']:<18} [{tags}] {i['title'][:80]}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("feed", type=Path)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)
    if not (args.feed / "meta.json").exists():
        print(f"{args.feed}: no meta.json (not a feed output folder)", file=sys.stderr)
        return 2
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(errors="replace")  # titles may contain characters the console cannot show
    print(summarize(args.feed, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())

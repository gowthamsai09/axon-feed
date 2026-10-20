"""Summarise a feed output folder so tagging and ranking can be reviewed by eye.

python -m scripts.inspect_feed ..\\feed-out [--top 15] [--untagged 25] [--examples 2]
python -m scripts.inspect_feed ..\\feed-out --tag agents-tooling [--limit 30]
python -m scripts.inspect_feed ..\\feed-out --entity Apple [--limit 30]

The drill-down forms list the items behind one tag or entity (title and snippet), which is how
false positives are found when tuning config/taxonomy.yaml.
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


def _one_line(text: str, width: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def _load_items(feed: Path) -> list[dict[str, Any]]:
    items = [_load(p) for p in sorted((feed / "items").glob("*.json"))]
    items.sort(key=lambda i: (-i["base_score"], i["id"]))
    return items


def drill_down(
    feed: Path, *, tag: str | None = None, entity: str | None = None, limit: int = 30
) -> str:
    """Items behind one tag or entity (entity match is case-insensitive on the entity name)."""
    items = _load_items(feed)
    if tag is not None:
        hits = [i for i in items if tag in i["tags"]]
        label = f"TAG {tag}"
    else:
        wanted = (entity or "").casefold()
        hits = [i for i in items if wanted in {e.casefold() for e in i.get("entities", [])}]
        label = f"ENTITY {entity}"
    out = [f"{label}: {len(hits)} of {len(items)} items"]
    for i in hits[:limit]:
        out.append(f"  {i['source_id']:<18} {_one_line(i['title'], 90)}")
        out.append(f"      {_one_line(i.get('snippet', ''), 160)}")
    if len(hits) > limit:
        out.append(f"  ... {len(hits) - limit} more (raise --limit)")
    return "\n".join(out)


def summarize(feed: Path, top: int = 15, untagged: int = 25, examples: int = 2) -> str:
    meta = _load(feed / "meta.json")
    items = _load_items(feed)
    out = [
        f"generated {meta['generated_at']}  window {meta['window_hours']:g}h",
        f"counts {meta['counts']}",
        "",
        "SOURCES",
    ]
    for s in meta["sources"]:
        out.append(
            f"  {s['status']:<7} {s['id']:<22} fetched={s['fetched']:<5} "
            f"collected={s['collected']:<5} in_window={s['in_window']:<5} "
            f"retained={s.get('retained', 0)}"
        )
    out += ["", f"WARNINGS ({len(meta['warnings'])})"] + [f"  {w}" for w in meta["warnings"][:20]]
    tag_counts = Counter(t for i in items for t in i["tags"])
    out += ["", "TAGS (items per topic; share of all items)"]
    out += [f"  {n:>4}  {n / max(len(items), 1):>4.0%}  {t}" for t, n in tag_counts.most_common()]
    no_tag = [i for i in items if not i["tags"]]
    out += ["", f"ITEMS WITHOUT ANY TAG: {len(no_tag)} of {len(items)}"]
    no_tag_src = Counter(i["source_id"] for i in no_tag)
    out += ["  by source: " + ", ".join(f"{s}={n}" for s, n in no_tag_src.most_common())]
    for i in no_tag[:untagged]:
        out.append(f"  {i['source_id']:<18} {_one_line(i['title'], 90)}")
        out.append(f"      {_one_line(i.get('snippet', ''), 160)}")
    if len(no_tag) > untagged:
        out.append(f"  ... {len(no_tag) - untagged} more (raise --untagged)")
    ent = Counter(e for i in items for e in i.get("entities", []))
    out += ["", f"ENTITIES ({len(ent)}; example titles)"]
    for e, n in ent.most_common():
        out.append(f"  {n:>4}  {e}")
        shown = [i for i in items if e in i.get("entities", [])][:examples]
        out += [f"        - {_one_line(i['title'], 80)}" for i in shown]
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
    ap.add_argument("--untagged", type=int, default=25, help="untagged items to list")
    ap.add_argument("--examples", type=int, default=2, help="example titles per entity")
    ap.add_argument("--tag", help="drill down: list the items carrying this topic slug")
    ap.add_argument("--entity", help="drill down: list the items carrying this entity name")
    ap.add_argument("--limit", type=int, default=30, help="items shown in a drill-down")
    ap.add_argument(
        "--save", type=Path, help="write the report here as UTF-8 (avoids console codepages)"
    )
    args = ap.parse_args(argv)
    if not (args.feed / "meta.json").exists():
        print(f"{args.feed}: no meta.json (not a feed output folder)", file=sys.stderr)
        return 2
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(errors="replace")  # titles may contain characters the console cannot show
    if args.tag or args.entity:
        report = drill_down(args.feed, tag=args.tag, entity=args.entity, limit=args.limit)
    else:
        report = summarize(args.feed, args.top, args.untagged, args.examples)
    if args.save:
        args.save.write_text(report + "\n", encoding="utf-8")
        print(f"wrote {args.save}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

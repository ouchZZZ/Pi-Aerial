"""Reconcile the Apple inventory with persistent Pi-Aerial identities."""
from __future__ import annotations

import hashlib
from . import apple_catalog


def clip_id(provider, source_id):
    return hashlib.sha256((provider + ":" + source_id).encode()).hexdigest()[:24]


def reconcile(groups, existing, extras, valid_url):
    """Pure reconciliation. Keep legacy IDs/providers and never discard an old row.

    If several previously published IDs now match one scene, defer that
    ambiguous migration rather than losing a user's selection or blacklist.
    """
    previous = {row["id"]: row for row in existing}
    for row in extras:
        previous.setdefault(row["id"], row)
    result = dict(previous)
    upgrades = {}
    conflicts = 0
    for group in groups:
        aliases = {clip_id(p, i) for p in ("apple", "apple_extras")
                   for i in group["apple_ids"]}
        keys = apple_catalog._identity_keys(group)
        matches = []
        for old in previous.values():
            record = {"apple_ids": old.get("apple_ids", []),
                      "versions": {old["remote_url"]: 0, **old.get("apple_versions", {})},
                      "shot_ids": old.get("apple_shot_ids", [])}
            if (old["id"] in aliases or keys & apple_catalog._identity_keys(record)):
                matches.append(old)
        if len(matches) > 1:
            conflicts += 1
            continue
        old = matches[0] if matches else None
        provider = old["provider"] if old else "apple"
        versions = {u: rank for u, rank in group["versions"].items()
                    if valid_url(u, provider)}
        if not versions:
            continue
        if old:
            for u, rank in old.get("apple_versions", {}).items():
                if valid_url(u, provider):
                    versions[u] = max(rank, versions.get(u, -1))
            url = old["remote_url"]
            old_rank = versions.get(url, apple_catalog.quality("", url))
            versions[url] = old_rank
        urls = sorted(versions, key=lambda u: (-versions[u], u))
        best = urls[0]
        if old:
            ident = old["id"]
            url = old["remote_url"]
            if versions[best] > old_rank:
                upgrades[ident] = best
            # Do not change the primary until the caller can safely invalidate.
            item = dict(old)
        else:
            source_id = next(iter(group["apple_ids"]), best)
            ident = clip_id(provider, source_id)
            url = best
            item = {"id": ident, "provider": provider, "remote_url": url,
                    "credit": "Apple Aerial", "page": "https://www.apple.com/tv/"}
        item["apple_ids"] = list(dict.fromkeys(item.get("apple_ids", []) + group["apple_ids"]))
        item["apple_shot_ids"] = list(dict.fromkeys(item.get("apple_shot_ids", []) + group.get("shot_ids", [])))
        item["apple_sources"] = list(dict.fromkeys(item.get("apple_sources", []) + group["sources"]))
        item["apple_versions"] = versions
        item["alternate_urls"] = [u for u in urls if u != url]
        item["name"] = item.get("name") or str(group["name"] or ident)[:110]
        item["name_en"] = item.get("name_en") or str(group["name_en"] or ident)[:110]
        captions = [{"second": int(sec), "text": text[:1200]}
                    for sec, text in sorted(group["captions"].items(), key=lambda kv: int(kv[0]))
                    if isinstance(text, str)]
        if captions and not item.get("captions"):
            item["captions"] = captions
        if not item.get("description") and captions:
            item["description"] = captions[0]["text"]
        result[ident] = item
    return list(result.values()), upgrades, conflicts

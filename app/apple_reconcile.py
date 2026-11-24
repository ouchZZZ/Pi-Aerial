"""Reconcile the Apple inventory with persistent Pi-Aerial identities."""
from __future__ import annotations

import hashlib
from . import apple_catalog


def clip_id(provider, source_id):
    return hashlib.sha256((provider + ":" + source_id).encode()).hexdigest()[:24]


def legacy_aliases(groups, existing, protected):
    """Find already-published obsolete aliases of a single modern TV scene.

    Do not collapse ambiguous canonical IDs or retire a cached/in-use alias.
    This handles the historical tvOS 11/13 duplicates without discarding
    user media. Return alias -> canonical ID; caller migrates journals.
    """
    retired = {}
    for group in groups:
        codes = apple_catalog.scene_codes(group)
        if len(codes) != 1 or "tvos26" not in group["sources"]:
            continue
        def matches(row):
            return apple_catalog.scene_codes({
                "shot_ids": row.get("apple_shot_ids", []),
                "versions": {row["remote_url"]: 0, **row.get("apple_versions", {})},
            }) == codes

        candidates = [row for row in existing if matches(row)]
        modern = [row for row in candidates if "tvos26" in row.get("apple_sources", [])]
        if len(modern) != 1:
            continue
        canonical = modern[0]["id"]
        for row in candidates:
            ident = row["id"]
            sources = set(row.get("apple_sources", []))
            if (ident != canonical and sources and sources <= {"tvos11", "tvos13"}
                    and not row.get("apple_shot_ids") and not protected(ident)):
                retired[ident] = canonical
    return retired


def reconcile(groups, existing, extras, valid_url):
    """Pure reconciliation. Keep legacy IDs/providers and never discard an old row.

    If several previously published IDs now match one scene, defer that
    ambiguous migration rather than losing a user's selection or blacklist.
    """
    previous = {row["id"]: row for row in existing}
    published_ids = set(previous)
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
        published = [old for old in matches if old["id"] in published_ids]
        if len(published) > 1:
            conflicts += 1
            continue
        old = published[0] if published else (matches[0] if matches else None)
        # A bundled fallback is not a second published clip. If a real entry
        # already represents it, omit the redundant proposal, not any file.
        for redundant in matches:
            if redundant is not old and redundant["id"] not in published_ids:
                result.pop(redundant["id"], None)
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
            # A higher compatibility rank is not proof of better pixels.
            # Only replace demonstrably sub-target resolution or an HDR-only
            # source with a known SDR source for our 1080p SDR output.
            # Do not mass-replace existing 1080p/4K SDR or unknown renditions.
            if old_rank in (0, 1) and versions[best] >= 3:
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
        item["apple_alias_ids"] = sorted(aliases | {ident} | set(item.get("apple_alias_ids", [])))
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

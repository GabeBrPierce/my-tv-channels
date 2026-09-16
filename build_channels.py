#!/usr/bin/env python3
"""
build_channels.py

Pulls channel/stream/logo/language data from the iptv-org project
(https://github.com/iptv-org/iptv — CC0/Unlicense, links-only, no hosted
video) and writes a channels.json in THIS app's schema (see
requirements.md section 3.1 / channels.example.json).

Intended use: run this in a scheduled GitHub Action in your own repo (e.g.
nightly) so channels.json stays current, or run it locally when you want to
refresh the list by hand. It has NOT been run/tested in the environment that
generated this scaffold — that sandbox's network policy blocks
iptv-org.github.io, so treat this as a starting point to verify on your own
machine or in CI before trusting it.

Usage:
    python3 build_channels.py --languages eng spa --output channels.json

Note: iptv-org uses ISO 639-2/3 codes (e.g. "eng", "spa"), not ISO 639-1
("en", "es").
"""

import argparse
import json
import sys
import urllib.request

CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
STREAMS_URL = "https://iptv-org.github.io/api/streams.json"
LOGOS_URL = "https://iptv-org.github.io/api/logos.json"
FEEDS_URL = "https://iptv-org.github.io/api/feeds.json"
GUIDES_URL = "https://iptv-org.github.io/api/guides.json"


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def build(languages, categories_filter, require_subtitles_for_non_target):
    print("Fetching channels.json ...", file=sys.stderr)
    channels = fetch_json(CHANNELS_URL)
    print("Fetching streams.json ...", file=sys.stderr)
    streams = fetch_json(STREAMS_URL)
    print("Fetching logos.json ...", file=sys.stderr)
    logos = fetch_json(LOGOS_URL)
    print("Fetching feeds.json ...", file=sys.stderr)
    feeds = fetch_json(FEEDS_URL)
    print("Fetching guides.json ...", file=sys.stderr)
    guides = fetch_json(GUIDES_URL)

    streams_by_channel = {}
    for s in streams:
        streams_by_channel.setdefault(s.get("channel"), []).append(s)

    logos_by_channel = {}
    for l in logos:
        logos_by_channel.setdefault(l.get("channel"), []).append(l)

    feeds_by_channel = {}
    for f in feeds:
        feeds_by_channel.setdefault(f.get("channel"), []).append(f)

    guides_by_channel = {}
    for g in guides:
        guides_by_channel.setdefault(g.get("channel"), []).append(g)

    out_channels = []

    for ch in channels:
        ch_id = ch.get("id")
        ch_streams = streams_by_channel.get(ch_id, [])
        if not ch_streams:
            continue  # no known working stream URL — skip

        ch_feeds = feeds_by_channel.get(ch_id, [])
        languages_for_channel = set()
        for f in ch_feeds:
            for lang in f.get("languages", []) or []:
                languages_for_channel.add(lang)

        # Fall back to no language info -> treat as unknown, exclude unless
        # explicitly requested via categories_filter override.
        if languages.__len__() > 0 and not (languages_for_channel & set(languages)):
            # Not in a requested language: only keep if it's flagged as
            # having subtitles available (iptv-org doesn't track subtitles
            # directly, so this app's `hasSubtitles` field is left False by
            # default here — set it manually afterward for channels you've
            # confirmed have burned-in or selectable subtitles).
            if require_subtitles_for_non_target:
                continue

        logo_url = None
        ch_logos = logos_by_channel.get(ch_id, [])
        if ch_logos:
            logo_url = ch_logos[0].get("url")

        epg_source = None
        ch_guides = guides_by_channel.get(ch_id, [])
        if ch_guides:
            # iptv-org's own /epg tool is what actually turns these into
            # XMLTV; this just records that a guide source exists.
            epg_source = ch_guides[0].get("site")

        primary_lang = next(iter(languages_for_channel), "en")

        out_channels.append({
            "id": ch_id,
            "name": ch.get("name"),
            "streamUrl": ch_streams[0].get("url"),
            "logoUrl": logo_url,
            "category": ",".join(ch.get("categories", []) or []),
            "language": primary_lang,
            "hasSubtitles": False,  # confirm manually per channel; see note above
            "epgSource": epg_source,
            "enabled": True,
        })

    return {
        "schemaVersion": 1,
        "updatedAt": None,  # fill in with current UTC timestamp at write time
        "channels": out_channels,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--languages", nargs="*", default=["eng"],
                         help="ISO 639-2/3 language codes to include by default (e.g. eng spa)")
    parser.add_argument("--output", default="channels.json")
    parser.add_argument("--include-non-target-without-subtitles", action="store_true",
                         help="Include channels outside --languages even without confirmed subtitles "
                              "(off by default, matching this app's language-filter rule)")
    args = parser.parse_args()

    import datetime
    data = build(
        languages=args.languages,
        categories_filter=None,
        require_subtitles_for_non_target=not args.include_non_target_without_subtitles,
    )
    data["updatedAt"] = datetime.datetime.utcnow().isoformat() + "Z"

    with open(args.output, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Wrote {len(data['channels'])} channels to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

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
import concurrent.futures
import json
import sys
import urllib.error
import urllib.request

CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
STREAMS_URL = "https://iptv-org.github.io/api/streams.json"
LOGOS_URL = "https://iptv-org.github.io/api/logos.json"
FEEDS_URL = "https://iptv-org.github.io/api/feeds.json"
GUIDES_URL = "https://iptv-org.github.io/api/guides.json"


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def stream_is_live(url, timeout):
    """Best-effort liveness check: GET the URL and read a few bytes.

    iptv-org's list has significant link rot (dead hosts, geo-blocks,
    expired paths) — see free-resources.md section 1. This isn't a
    guarantee the stream will actually play (geo-blocking in particular
    can pass here and still fail for a real viewer in a different
    region), just a cheap filter for streams that are unambiguously dead.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; StreamCheck/1.0)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(1024)
            return 200 <= resp.status < 300
    except Exception:
        return False


def filter_live_streams(candidates, concurrency, timeout):
    """candidates: list of (channel_id, url). Returns the set of channel_ids
    whose stream responded successfully."""
    live_ids = set()
    total = len(candidates)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_id = {
            pool.submit(stream_is_live, url, timeout): ch_id
            for ch_id, url in candidates
        }
        for future in concurrent.futures.as_completed(future_to_id):
            done += 1
            if done % 100 == 0:
                print(f"  verified {done}/{total} streams...", file=sys.stderr)
            if future.result():
                live_ids.add(future_to_id[future])
    return live_ids


def load_existing_numbers(output_path):
    """Best-effort read of a previously-written channels.json so channel
    `number`s survive regeneration. Numbers are what the app's numeric
    keypad entry and on-screen banner use — if they shifted every time the
    list was rebuilt (e.g. because a dead stream earlier in the list got
    dropped and everything after it shifted up by one), "channel 12" would
    mean something different every day. Assigning each id a number once and
    reusing it forever avoids that, independent of ordering or of channels
    going enabled=false and back.
    """
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (OSError, ValueError):
        return {}
    numbers = {}
    for ch in old_data.get("channels", []):
        if isinstance(ch.get("number"), int) and ch.get("id"):
            numbers[ch["id"]] = ch["number"]
    return numbers


def build(languages, categories_filter, require_subtitles_for_non_target,
          verify_streams=False, verify_concurrency=40, verify_timeout=7,
          existing_numbers=None):
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
    existing_numbers = existing_numbers or {}
    next_number = (max(existing_numbers.values()) + 1) if existing_numbers else 1

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

        if ch_id in existing_numbers:
            number = existing_numbers[ch_id]
        else:
            number = next_number
            next_number += 1

        out_channels.append({
            "id": ch_id,
            "number": number,
            "name": ch.get("name"),
            "streamUrl": ch_streams[0].get("url"),
            "logoUrl": logo_url,
            "category": ",".join(ch.get("categories", []) or []),
            "language": primary_lang,
            "hasSubtitles": False,  # confirm manually per channel; see note above
            "epgSource": epg_source,
            "enabled": True,
        })

    out_channels.sort(key=lambda ch: ch["number"])

    if verify_streams:
        print(f"Verifying {len(out_channels)} stream URLs ({verify_concurrency} at a time, "
              f"{verify_timeout}s timeout each) — this takes a few minutes...", file=sys.stderr)
        candidates = [(ch["id"], ch["streamUrl"]) for ch in out_channels]
        live_ids = filter_live_streams(candidates, verify_concurrency, verify_timeout)
        # Mark dead streams unavailable rather than dropping them, so a
        # channel's number and position stay put while its source is dark
        # and it simply reappears (same number) once verified live again —
        # see load_existing_numbers() above for why that matters.
        newly_dead = 0
        for ch in out_channels:
            live = ch["id"] in live_ids
            if ch["enabled"] and not live:
                newly_dead += 1
            ch["enabled"] = live
        still_live = sum(1 for ch in out_channels if ch["enabled"])
        print(f"{newly_dead} streams unreachable this run; {still_live}/{len(out_channels)} "
              f"channels currently enabled.", file=sys.stderr)

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
    parser.add_argument("--verify-streams", action="store_true",
                         help="GET each candidate stream URL and drop ones that don't respond. "
                              "iptv-org's list has significant link rot — see free-resources.md "
                              "section 1 — so this is recommended before publishing. Adds a few "
                              "minutes to the run.")
    parser.add_argument("--verify-concurrency", type=int, default=40,
                         help="Parallel stream checks when --verify-streams is set (default: 40)")
    parser.add_argument("--verify-timeout", type=float, default=7,
                         help="Per-stream timeout in seconds when --verify-streams is set (default: 7)")
    args = parser.parse_args()

    import datetime
    data = build(
        languages=args.languages,
        categories_filter=None,
        require_subtitles_for_non_target=not args.include_non_target_without_subtitles,
        verify_streams=args.verify_streams,
        verify_concurrency=args.verify_concurrency,
        verify_timeout=args.verify_timeout,
        existing_numbers=load_existing_numbers(args.output),
    )
    data["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    with open(args.output, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Wrote {len(data['channels'])} channels to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

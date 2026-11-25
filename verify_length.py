#!/usr/bin/env python3
"""verify_length.py — prove a media file's real length, at packet level.

A container header will happily report the duration a file was
SUPPOSED to have. If an encode was interrupted — a crashed process, a
killed session, a disk hiccup — the header can still claim the full
length while the file actually holds a fraction of it. Header
duration is a claim, not a proof.

The proof is in the packets: read every packet's presentation
timestamp (pts) and take the maximum, separately for the video and
audio streams. The largest pts is where the content actually ends.
If both streams end where the file claims to end, the length is real.

Usage:
    python3 verify_length.py master.mkv
    python3 verify_length.py master.mkv --expect 3600

With --expect, the exit code is 0 only when both streams reach the
expected length (within --tolerance seconds, default 1.0). Without
--expect it just reports. Either way it prints the header's claimed
duration next to the measured packet ends, so a lying header is
obvious at a glance.

Requires: ffprobe on PATH, python3 (standard library only).
"""
import argparse
import subprocess
import sys


def header_duration(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return None


def stream_end(path, stream):
    """(packet count, max pts in seconds) for one stream selector."""
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    count, max_pts = 0, 0.0
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pts = float(line)
        except ValueError:
            continue
        count += 1
        if pts > max_pts:
            max_pts = pts
    return count, max_pts


def main():
    ap = argparse.ArgumentParser(
        description="Prove a media file's length from packet timestamps, "
                    "not from its header.")
    ap.add_argument("file")
    ap.add_argument("--expect", type=float, default=None,
                    help="expected length in seconds; fail if a stream "
                         "ends earlier than this (minus tolerance)")
    ap.add_argument("--tolerance", type=float, default=1.0)
    cfg = ap.parse_args()

    claimed = header_duration(cfg.file)
    print(f"file: {cfg.file}")
    print(f"header claims: {claimed:.3f}s" if claimed is not None
          else "header claims: (no duration in header)")

    ends = {}
    for label, sel in (("video", "v:0"), ("audio", "a:0")):
        count, end = stream_end(cfg.file, sel)
        if count:
            ends[label] = end
            print(f"{label}: {count} packets, content ends at {end:.3f}s")
        else:
            print(f"{label}: no packets found")

    if not ends:
        print("no streams could be read — nothing is proven")
        return 1

    ok = True
    if cfg.expect is not None:
        floor = cfg.expect - cfg.tolerance
        for label, end in ends.items():
            if end < floor:
                print(f"FAIL: {label} ends at {end:.3f}s, short of the "
                      f"expected {cfg.expect:.1f}s")
                ok = False
        if ok:
            print(f"PROVEN: every stream reaches the expected "
                  f"{cfg.expect:.1f}s (within {cfg.tolerance:.1f}s)")
    if claimed is not None:
        for label, end in ends.items():
            if abs(end - claimed) > max(cfg.tolerance, 2.0):
                print(f"note: {label} content ends {claimed - end:+.1f}s "
                      f"from the header's claim")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""sequence.py — sequence songs into one exact-length mix.

Takes a list of songs IN ORDER, joins them with smooth crossfades
(a few seconds of overlap between every pair, so the mix never has a
gap, a hard cut, or a pocket of silence between tracks), trims the
result to an exact target length, ends it with a gentle fade-out, and
sets the level the pristine way: ONE static gain, chosen so the
finished mix's true peak sits at -1 dBTP. No dynamic loudness
processing, no compressor, no limiter — the dynamics in the music are
the music, and they ship untouched. See docs/pristine-master.md for
the full reasoning.

The output is a lossless 24-bit WAV master. A JSON build record is
written next to it with the raw measurements, the gain that was
applied, and the verification numbers for the finished file:

- exact duration (must equal the target)
- true peak after the gain (must sit at about -1 dBTP)
- silence scan of the composed mix: silences of 2s or more at -45 dB
  (must be zero — this is the check that proves the crossfades left
  no gaps)

Usage:
    python3 sequence.py song1.mp3 song2.mp3 ... --target 3600 --out mix.wav

If the songs together (after crossfade overlaps) are shorter than the
target, the build stops and tells you by how much — add more songs.
An hour of mix means an hour of distinct music.

Requires: ffmpeg and ffprobe on PATH, python3 (standard library only).
"""
import argparse
import json
import os
import re
import subprocess
import sys

TARGET_PEAK_DBTP = -1.0


def run(cmd):
    subprocess.run(cmd, check=True)


def probe_duration(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True)
    return float(p.stdout.strip())


def measure(path):
    """Integrated loudness / true peak / LRA of a file, using ffmpeg's
    loudnorm filter purely as a measuring instrument."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-vn",
         "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True)
    d = json.loads(re.search(r'\{[^}]*"input_i"[^}]*\}', p.stderr, re.S).group(0))
    return float(d["input_i"]), float(d["input_tp"]), float(d["input_lra"])


def silence_scan(path, noise_db=-45, min_dur=2):
    p = subprocess.run(
        ["ffmpeg", "-i", path, "-af",
         f"silencedetect=noise={noise_db}dB:d={min_dur}",
         "-f", "null", "-"],
        capture_output=True, text=True)
    starts = [ln for ln in p.stderr.splitlines() if "silence_start:" in ln]
    return len(starts)


def main():
    ap = argparse.ArgumentParser(
        description="Sequence songs with crossfades into an exact-length, "
                    "gain-only-leveled lossless master.")
    ap.add_argument("files", nargs="+", help="songs, in play order")
    ap.add_argument("--target", type=float, default=3600.0,
                    help="exact final length in seconds (default 3600)")
    ap.add_argument("--crossfade", type=float, default=4.0,
                    help="crossfade overlap between songs, seconds (default 4)")
    ap.add_argument("--fade-out", type=float, default=15.0,
                    help="ending fade-out length, seconds (default 15)")
    ap.add_argument("--sample-rate", type=int, default=48000)
    ap.add_argument("--out", default="mix.wav", help="output WAV path")
    ap.add_argument("--record", default=None,
                    help="build record JSON path (default: <out>.json)")
    cfg = ap.parse_args()

    if len(cfg.files) < 2:
        print("need at least two songs to build a mix", file=sys.stderr)
        return 2
    record_path = cfg.record or (cfg.out + ".json")
    raw_path = cfg.out + ".raw.wav"

    # ---- pass 1: render the crossfade chain, untouched ----------------
    args = []
    for f in cfg.files:
        args += ["-i", f]
    fc = ""
    for i in range(len(cfg.files)):
        fc += f"[{i}:a]aresample={cfg.sample_rate}[a{i}];"
    cur = "a0"
    for i in range(1, len(cfg.files)):
        fc += (f"[{cur}][a{i}]acrossfade=d={cfg.crossfade}"
               f":c1=tri:c2=tri[x{i}];")
        cur = f"x{i}"
    fc += f"[{cur}]anull[aout]"
    run(["ffmpeg", "-y", "-v", "error"] + args +
        ["-filter_complex", fc, "-map", "[aout]",
         "-c:a", "pcm_f32le", "-ar", str(cfg.sample_rate), "-ac", "2",
         raw_path])

    raw_dur = probe_duration(raw_path)
    if raw_dur < cfg.target:
        print(f"the songs come to {raw_dur:.1f}s after crossfades, "
              f"{cfg.target - raw_dur:.1f}s short of the {cfg.target:.0f}s "
              f"target — add more songs and rebuild", file=sys.stderr)
        return 1

    # ---- measure the composed chain, pick ONE static gain -------------
    raw_i, raw_tp, raw_lra = measure(raw_path)
    gain = TARGET_PEAK_DBTP - raw_tp
    print(f"composed chain: {raw_i:.2f} LUFS, LRA {raw_lra:.1f} LU, "
          f"true peak {raw_tp:+.2f} dBTP -> static gain {gain:+.2f} dB")

    # ---- pass 2: trim to exact length, fade out, apply the gain -------
    fade_start = cfg.target - cfg.fade_out
    run(["ffmpeg", "-y", "-v", "error", "-i", raw_path,
         "-af", (f"atrim=0:{cfg.target},"
                 f"afade=t=out:st={fade_start}:d={cfg.fade_out},"
                 f"volume={gain:.3f}dB"),
         "-c:a", "pcm_s24le", "-ar", str(cfg.sample_rate), "-ac", "2",
         cfg.out])
    os.remove(raw_path)

    # ---- verify the finished master by measurement --------------------
    final_dur = probe_duration(cfg.out)
    fin_i, fin_tp, fin_lra = measure(cfg.out)
    gaps = silence_scan(cfg.out)
    record = {
        "tracks": [os.path.basename(f) for f in cfg.files],
        "crossfade_s": cfg.crossfade,
        "target_s": cfg.target,
        "raw_chain": {"lufs_integrated": round(raw_i, 2),
                      "lra_lu": round(raw_lra, 1),
                      "true_peak_dbtp": round(raw_tp, 2)},
        "static_gain_db": round(gain, 3),
        "final": {"duration_s": round(final_dur, 3),
                  "lufs_integrated": round(fin_i, 2),
                  "lra_lu": round(fin_lra, 1),
                  "true_peak_dbtp": round(fin_tp, 2),
                  "silences_ge_2s_at_-45db": gaps},
        "checks": {
            "exact_length": abs(final_dur - cfg.target) < 0.1,
            "peak_at_-1dbtp": abs(fin_tp - TARGET_PEAK_DBTP) < 0.15,
            "no_gaps": gaps == 0,
        },
    }
    with open(record_path, "w") as fh:
        json.dump(record, fh, indent=1)
    print(f"final master: {final_dur:.3f}s, {fin_i:.2f} LUFS, "
          f"true peak {fin_tp:+.2f} dBTP, gaps: {gaps}")
    print(f"build record: {record_path}")
    ok = all(record["checks"].values())
    print("ALL CHECKS PASS" if ok else "CHECKS FAILED — see build record")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

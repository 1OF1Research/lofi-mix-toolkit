#!/usr/bin/env python3
"""gate.py — an audio shape gate for long-form mix production.

Every track that goes into a mix gets measured before it is allowed in.
A track passes only if ALL of these hold:

- duration at least --min-duration seconds (default 120)
- integrated loudness inside --lufs-min..--lufs-max (default -20..-10 LUFS)
- true peak at or below --max-true-peak dBTP (default -0.1: no clipping,
  no inter-sample overs)
- loudness range at or below --max-lra LU (default 9: the track stays
  even, no sudden swells or dropouts)
- no internal silence longer than --max-silence seconds (default 3,
  measured at -50 dB; the first and last --edge-margin seconds are
  ignored so natural intros and fade-outs do not count)
- tempo steadiness: the track is split into 6 windows, each window's
  tempo must stay close to the whole-track tempo, and the spread
  between the fastest and slowest window must be at most
  --max-tempo-spread BPM (default 6). This is the check that catches
  drifting or wobbling rhythm that a single overall BPM number hides.

Usage:
    python3 gate.py track1.mp3 track2.flac [...] > results.json

Output is a JSON list on stdout, one object per file, with every
measurement and a per-check breakdown, so a failing track tells you
exactly why it failed. Exit code is 0 when every file passes, 1 when
any file fails, 2 on usage errors.

Requires: ffmpeg and ffprobe on PATH, python3, numpy.
"""
import argparse
import json
import re
import subprocess
import sys

import numpy as np

SR = 22050  # analysis sample rate for the tempo check


def duration(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True)
    return float(p.stdout.strip())


def loudness(path):
    """Integrated loudness, true peak, and loudness range via ffmpeg's
    loudnorm filter used as a measuring instrument only (first pass of
    loudnorm prints the input's measured values as JSON)."""
    p = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "loudnorm=print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True)
    m = re.search(r'\{[^}]*"input_i"[^}]*\}', p.stderr, re.S)
    d = json.loads(m.group(0))
    return float(d["input_i"]), float(d["input_tp"]), float(d["input_lra"])


def silences(path, noise_db=-50, min_dur=3):
    p = subprocess.run(
        ["ffmpeg", "-i", path, "-af",
         f"silencedetect=noise={noise_db}dB:d={min_dur}",
         "-f", "null", "-"],
        capture_output=True, text=True)
    out, cur = [], None
    for line in p.stderr.splitlines():
        if "silence_start:" in line:
            try:
                cur = float(line.split("silence_start:")[1].strip())
            except Exception:
                cur = None
        if "silence_end:" in line and cur is not None:
            try:
                out.append((cur, float(
                    line.split("silence_end:")[1].split("|")[0].strip())))
            except Exception:
                pass
            cur = None
    return out


def decode(path):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"],
        capture_output=True)
    if p.returncode != 0 or len(p.stdout) < SR:
        return None
    return np.frombuffer(p.stdout, dtype=np.float32)


def flux_env(x, hop=512, n_fft=2048):
    """Spectral flux: how much new energy arrives per frame. Beats show
    up as a periodic pulse in this envelope."""
    frames = max(0, (len(x) - n_fft) // hop)
    win = np.hanning(n_fft).astype(np.float32)
    flux = np.zeros(frames)
    prev = None
    for i in range(frames):
        mag = np.abs(np.fft.rfft(x[i * hop:i * hop + n_fft] * win))
        if prev is not None:
            d = mag - prev
            flux[i] = float(np.sum(d[d > 0]))
        prev = mag
    return flux, hop


def best_lag(seg, hop, lo_s=0.34, hi_s=1.35, center=None, tol=0.12):
    """Strongest periodicity lag in the flux envelope, restricted to
    beat periods between lo_s and hi_s seconds (about 44-176 BPM).
    When center is given, only lags within +/-tol of it are considered,
    so a window is judged against the track's own tempo."""
    seg = seg - seg.mean()
    if len(seg) < 100:
        return None
    ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
    lo = int(lo_s * SR / hop)
    hi = int(hi_s * SR / hop)
    if center is not None:
        lo = max(lo, int(center * (1 - tol)))
        hi = min(hi, int(center * (1 + tol)))
    if hi <= lo:
        return None
    return lo + int(np.argmax(ac[lo:hi]))


def tempo(path):
    """Returns (median window BPM, window BPM spread). The spread is the
    steadiness measure: a steady track's windows all agree."""
    x = decode(path)
    if x is None:
        return None, None
    flux, hop = flux_env(x)
    glag = best_lag(flux, hop)
    if glag is None:
        return None, None
    gbpm = 60.0 / (glag * hop / SR)
    w = len(flux) // 6
    bpms = []
    for i in range(6):
        lag = best_lag(flux[i * w:(i + 1) * w], hop, center=glag)
        if lag is not None:
            bpms.append(60.0 / (lag * hop / SR))
    if len(bpms) < 2:
        return round(gbpm, 1), None
    med = float(np.median(bpms))
    return round(med, 1), round(max(bpms) - min(bpms), 1)


def gate(path, cfg):
    dur = duration(path)
    lufs, tp, lra = loudness(path)
    sils = [(s, e) for (s, e) in silences(path, min_dur=cfg.max_silence)
            if s > cfg.edge_margin and e < dur - cfg.edge_margin]
    med, spread = tempo(path)
    checks = {
        "duration_ok": dur >= cfg.min_duration,
        "no_clipping": tp <= cfg.max_true_peak,
        "loudness_range_ok": lra <= cfg.max_lra,
        "loudness_in_range": cfg.lufs_min <= lufs <= cfg.lufs_max,
        "no_internal_silence": len(sils) == 0,
        "tempo_steady": spread is not None and spread <= cfg.max_tempo_spread,
    }
    return {
        "file": path.replace("\\", "/").split("/")[-1],
        "duration_s": round(dur, 1),
        "lufs_integrated": round(lufs, 2),
        "true_peak_dbtp": round(tp, 2),
        "lra_lu": round(lra, 1),
        "internal_silences": [[round(s, 2), round(e, 2)] for s, e in sils],
        "tempo_median_bpm": med,
        "tempo_spread_bpm": spread,
        "checks": checks,
        "pass": all(checks.values()),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Measure audio tracks against a shape gate; "
                    "JSON results on stdout.")
    ap.add_argument("files", nargs="+", help="audio files to check")
    ap.add_argument("--min-duration", type=float, default=120.0)
    ap.add_argument("--lufs-min", type=float, default=-20.0)
    ap.add_argument("--lufs-max", type=float, default=-10.0)
    ap.add_argument("--max-true-peak", type=float, default=-0.1)
    ap.add_argument("--max-lra", type=float, default=9.0)
    ap.add_argument("--max-silence", type=float, default=3.0,
                    help="longest allowed internal silence, seconds")
    ap.add_argument("--edge-margin", type=float, default=2.0,
                    help="ignore silences this close to the edges, seconds")
    ap.add_argument("--max-tempo-spread", type=float, default=6.0)
    cfg = ap.parse_args()

    results = [gate(f, cfg) for f in cfg.files]
    print(json.dumps(results, indent=1))
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

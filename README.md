# Lofi Mix Toolkit

The production tools behind a daily one-hour lofi video series —
published so anyone making long-form mixes can use them too.

Making an hour-long mix that actually holds up on good speakers comes
down to three disciplines: check every track before it earns a place
in the mix, set the final level without squashing the music, and
prove the finished file is really as long as it claims to be. Each
tool here covers one of those.

## The tools

### `gate.py` — check every track before it ships

Measures each audio file and reports, as JSON, whether it passes on
every count:

- **Duration** — long enough to be worth sequencing
- **Loudness** — integrated LUFS inside a sane range, and a loudness
  range (LRA) small enough that the track stays even
- **True peak** — no clipping, no inter-sample overs
- **Silence** — no unexplained gaps inside the track
- **Tempo steadiness** — the track is measured in six windows, and
  the windows have to agree with each other. A single overall BPM can
  look fine while the rhythm quietly drifts or wobbles; the window
  spread is what catches it

A failing track tells you exactly which check it failed, so you can
fix it or pick the other take instead of guessing.

```sh
python3 gate.py track1.mp3 track2.mp3 > gate-results.json
```

Every threshold is a command-line option with the defaults we use in
production. Exit code is 0 only when everything passes, so it drops
straight into a build script.

### `sequence.py` — build the hour

Sequences songs in the order you give them, joined with smooth
crossfades — a few seconds of overlap between every pair, so the mix
never has a gap, a hard cut, or a pocket of silence — then trims to
an exact target length (3600 seconds for the full hour) and finishes
with a gentle fade-out.

The level is set the pristine way: the composed chain is measured,
and **one static gain** is applied to the whole mix so its true peak
sits at −1 dBTP. No dynamic loudness processing, no compressor, no
limiter. The output is a lossless 24-bit WAV master, plus a JSON
build record holding the measurements before and after, the gain
that was applied, and the verification results: exact duration,
final true peak, and a silence scan of the composed mix (zero gaps
of 2 seconds or more at −45 dB, or the build reports the failure).

```sh
python3 sequence.py song1.mp3 song2.mp3 ... --target 3600 --out mix.wav
```

If the songs come up short of the target after crossfades, the build
stops and tells you by how much. An hour of mix means an hour of
distinct music — never one song stretched or looped to fill the time.

### `verify_length.py` — prove the length

A media file's header will report the duration the file was supposed
to have, even if the encode was interrupted and the file actually
holds half of that. This tool reads every packet's timestamp in the
video and audio streams and reports where the content really ends,
next to the header's claim.

```sh
python3 verify_length.py master.mkv --expect 3600
```

Exit code is 0 only when both streams genuinely reach the expected
length. Run it on every master before you publish — a two-hour title
on a forty-minute file is exactly the kind of mistake this catches.

### `docs/pristine-master.md` — the method

The full finishing method, written out so it can be applied with any
toolchain: the gain-only level recipe and why dynamic normalization
hurts gentle music, lossless-master discipline, the packet-level
length proof, and the final measurement checklist.

## How the pieces fit

1. Generate or gather more candidate tracks than the hour needs.
2. Run everything through `gate.py`. Only passing tracks go in;
   keep the passing spares for a later mix.
3. Order the passing tracks deliberately — a welcoming opener,
   variety through the middle, the calmest songs at the close.
4. Build with `sequence.py` and read its build record.
5. Mux the master (a looped picture plus the mix as FLAC audio in
   MKV works well), then run `verify_length.py` on the result.
6. Publish only when every number checks out.

## Requirements

- python3; `gate.py` also needs numpy (`pip install -r requirements.txt`)
- ffmpeg and ffprobe on PATH

## License

MIT — see [LICENSE](LICENSE). Use it, adapt it, ship your own mixes
with it.

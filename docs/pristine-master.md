# The pristine master method

How to finish a long-form mix so it sounds exactly like the music you
put into it — nothing squashed, nothing clipped, and a final file
whose length you have actually proven. This is the method the tools
in this repository implement, written out in full so you can apply it
with any toolchain.

It was learned the expensive way: early mixes went out with dynamics
processing on them and low-bitrate audio, and one shipped file even
measured *above* 0 dBTP — clipped, distorted, on the public page.
Every rule below exists because skipping it produced a measurably
worse file.

## 1. Set the level with one static gain, never dynamics

Compose the whole mix first — sequence, crossfades, ending fade —
then measure the composed chain and apply **one single gain** to the
whole thing, chosen so the true peak lands at **−1 dBTP**:

```
gain (dB) = −1.0 − (measured true peak of the composed chain, dBTP)
```

That is the entire loudness recipe. In particular:

- **No dynamic loudness normalization.** A single-pass dynamic
  normalizer rides the gain up and down continuously to hit a target.
  On gentle music that means the quiet passages get pumped up and the
  natural breathing of the track is flattened — the mix comes out
  sounding compressed even though no compressor was ever switched on.
- **No compressor, no limiter.** Dynamics are not a defect to be
  corrected; they are part of the recording. A static gain moves the
  whole performance up or down together and changes nothing inside it.
- **−1 dBTP of headroom** keeps inter-sample peaks safely below
  clipping through later encoding, with no audible cost.
- Measure with a loudness meter (ffmpeg's `loudnorm` filter prints
  integrated LUFS, true peak, and loudness range as JSON) — but use it
  as a *measuring instrument only*. The numbers decide the one gain;
  the filter never processes the shipped audio.

`sequence.py` in this repository does exactly this: it renders the
crossfade chain untouched, measures it, applies the single gain, and
records both sets of numbers in its build record.

## 2. Keep the master lossless, always

- The composed audio lives as WAV (or FLAC) at full quality. The
  master video file carries FLAC audio in an MKV container.
- Delivery copies — smaller files for size-capped upload routes,
  previews, anything else — are always *derived from* the master,
  never the other way around. If a derived copy is lost, the master
  regenerates it. If the master is lost, nothing regenerates it.
- When a size cap genuinely binds on some delivery route, spend the
  bytes on audio first: encode the (usually near-static) picture as
  lean as it will go and give the audio the highest bitrate that fits.
  Audio quality is never traded for picture bytes. And use the best
  route available — a cap on one route is a reason to find a better
  route, not a reason to ship worse sound.

## 3. Prove the length at packet level

A container header reports the duration the file was *supposed* to
have. If an encode is interrupted partway, the header can keep
claiming the full length while the file holds only a fraction of the
content — and quick spot checks can miss it, because seeking into a
damaged file may return a frame from wherever the data actually ends.

The reliable proof reads the packets themselves:

- For each stream (video and audio), read every packet's presentation
  timestamp and take the maximum. That maximum is where the content
  actually ends.
- Both streams must end at the claimed duration (within a second or
  so). If either ends early, the file is short no matter what the
  header says — rebuild it, and do not publish it under a title that
  promises the longer length.
- For long files built from a looped segment, prefer **stream copy**
  over re-encoding when you assemble the master (`ffmpeg -stream_loop
  -1 -i segment.mp4 -i mix.flac -c copy ...`): the picture is already
  encoded, copying is fast and bit-exact, and there is no long encode
  left running to be interrupted.

`verify_length.py` in this repository runs this proof and prints the
header's claim next to the measured packet ends.

## 4. Verify the shipped file by measurement, not by recipe

Before anything is published, measure the file exactly as it will
ship and check:

- integrated loudness, loudness range, and true peak of the final
  encoded file (true peak at about −1 dBTP, per rule 1);
- a silence scan of the whole length: zero silences of 2 seconds or
  more at −45 dB (this proves the crossfades left no gaps);
- the packet-level length proof from rule 3;
- the exact duration matches the target (3600.000s for a one-hour
  mix — not 3597, not "about an hour").

If a number is off, the file is not done, whatever the build log said.
Measure, fix, re-measure.

## The short version

1. Compose everything first.
2. Measure the composed chain; apply one static gain to −1 dBTP.
   Nothing dynamic ever touches the audio.
3. Master stays lossless; copies are derived, never the source.
4. Prove the length from packet timestamps, not the header.
5. Verify the final file by measurement before it ships.

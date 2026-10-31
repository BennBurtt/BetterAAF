# BetterAAF

Cleans up an AAF before the mix: duplicate clips, near-identical clips (null test) and stereo clips that are actually dual mono.

## How to use

1. Drop an `.aaf` on the window (or click the drop zone). Analysis starts on its own.
2. If audio files have moved, set **Audio files folder** (or drop the folder on the window) and analyze again.
3. Tick or untick clips in each tab, pick the actions, then **Export AAF…**. The original AAF is never modified.

## What it finds

- **Duplicates**: a clip fully covered, on another track, by a clip with the same audio. The higher track is kept.
- **Near-identical**: same, but the null test leaves a residual below the threshold (−60 dB by default).
- **Dual mono**: L − R below the threshold. Works with multichannel (Channel Combiner) stereo tracks,
  including DaVinci Resolve exports, and with `Track.L` / `Track.R` mono track pairs.

## Actions

- Delete or mute duplicates. Mute by moving to a muted “MUTE” track (default), clip gain at −144 dB,
  or Media Composer style disabled clip.
- Dual mono: make mono, deleting or muting the right channel. A stereo track that also holds true stereo
  clips keeps them, and its dual-mono clips move to a new “… MONO” track.
- **Self-contained AAF**: every audio file is embedded in one AAF.
- **Rename tracks by content** (on by default): Google's YAMNet model listens to every clip and each track is
  renamed Dialogue, Music, FX or Others. Extra categories can be ticked in the Tracks tab (Singing, Vocal sounds,
  Walla, Foley, Vehicles, Animals, Impacts, Ambience). Names can be edited before export.
- **Rename tracks DM for dual mono** (on by default): tracks coming from a dual-mono conversion get a “DM” suffix.
- **Levels** of the whole AAF played together: average / min / max in dBFS and LUFS (ITU-R BS.1770-4),
  plus true peak. Muted clips and tracks are skipped; clip gain, volume automation (clip and track level),
  pan and fades written in the AAF are applied. Plug-ins and buses are not included. DaVinci Resolve does not
  write its volume automation into AAFs, so Resolve exports are measured before the mix automation.

## Player

A read-only mini player (switch RESULTS / PLAYER above the results):

- Click anywhere on the timeline to move the playhead, Space to play / stop.
- Shift+S / Shift+M toggle solo / mute on the selected track (playback only, the export is not affected).
- Playhead modes: return to the clicked position after stopping, or stay where playback stopped.
- Speeds ×1, ×1.5, ×2, ×4, ×10. Fast dBFS peak meter on every track.
- Original / After BetterAAF: hear the AAF as it is, or as it will be exported with the current settings.
- Audio goes to the output selected in macOS, stereo L/R only.

## Known limits

- Clips moved to a new track (MUTE or MONO) lose their fades.
- Fades and clip gain are not taken into account by the null test.
- Level measurement only sees what the editing software wrote into the AAF (no plug-ins, no buses).
- MXF media (Media Composer) needs ffmpeg installed (`brew install ffmpeg`). WAV / BWF / AIFF are read directly.

## Development

`tests/make_test_aaf.py` builds a test AAF covering every case, `tests/check_aaf.py` runs a strict
consistency check (Pro Tools oriented). Every push builds the Mac app on GitHub and publishes it under Releases.

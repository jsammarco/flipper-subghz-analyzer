# Flipper Sub-GHz RAW Analyzer

A desktop GUI for inspecting, decoding, comparing, and lightly editing Flipper Zero `Sub-GHz` `RAW` capture files.

This project is aimed at reverse-engineering and analysis workflows where you want to:

- open a `.sub` capture from Flipper Zero
- split the raw pulse train into repeated frames
- try multiple decoding strategies against the same signal
- visualize mark/space timing as a waterfall
- compare many frames to spot fixed fields, button bits, IDs, rolling-code regions, and likely CRC/status tails
- edit decoded bits and write an updated `.sub` file back out

The app is implemented as a single Python/Tkinter GUI and uses only the Python standard library.

![Flipper Sub-GHz RAW Analyzer screenshot](/C:/Users/Joe/Projects/flipper-subghz-analyzer/screenshot.jpg)

## What It Does

The analyzer loads Flipper `RAW` `.sub` files, extracts `RAW_Data` pulse durations, and gives you several ways to inspect them:

- `Frame splitting`
  Detects long negative gaps and separates a capture into likely packet/frame boundaries.

- `Timing clustering`
  Estimates short-pulse and long-pulse timing clusters automatically from the loaded capture.

- `Multiple decode modes`
  Lets you reinterpret the same frame using several common pulse-width style decode strategies:
  - `PWM space: short=0 long=1`
  - `PWM pair: SS=0 SL=1`
  - `Manchester: SL=1 LS=0`
  - `Duration threshold`

- `Waterfall visualization`
  Displays each frame as a row of colored timing bars:
  - amber = mark
  - blue = space

- `Frame matching`
  Clicking a waterfall row highlights matching rows so repeated packets stand out immediately.

- `Bit / hex conversion`
  Converts decoded bitstrings to hex bytes and hex back to bits.

- `Signal structure analysis`
  Compares aligned decoded frames and labels likely regions such as:
  - preamble/sync
  - fixed header
  - device ID / serial
  - command or status bits
  - rolling/authentication block
  - CRC/checksum/status tail

- `Bit-to-pulse rewriting`
  Lets you modify decoded bits for a selected frame and rebuild that frame’s pulse train before saving a new `.sub`.

## Project Files

- [sub_analyzer_gui.py](/C:/Users/Joe/Projects/flipper-subghz-analyzer/sub_analyzer_gui.py)  
  Main Tkinter application.

- [Ceiling_fan.sub](/C:/Users/Joe/Projects/flipper-subghz-analyzer/Ceiling_fan.sub)
- [Ceiling_light.sub](/C:/Users/Joe/Projects/flipper-subghz-analyzer/Ceiling_light.sub)
- [Ceil_fan_up.sub](/C:/Users/Joe/Projects/flipper-subghz-analyzer/Ceil_fan_up.sub)
- [Ceil_fan_down.sub](/C:/Users/Joe/Projects/flipper-subghz-analyzer/Ceil_fan_down.sub)  
  Example Flipper Zero `RAW` captures included for testing.

- [screenshot.jpg](/C:/Users/Joe/Projects/flipper-subghz-analyzer/screenshot.jpg)  
  Screenshot of the UI.

- [LICENSE](/C:/Users/Joe/Projects/flipper-subghz-analyzer/LICENSE)  
  MIT license.

## Requirements

- Python `3.10+` recommended
- Tkinter available in your Python install

This project has no third-party Python dependencies.

## Getting Started

From the project folder, run the GUI:

```powershell
python sub_analyzer_gui.py
```

You can also open a capture immediately from the command line:

```powershell
python sub_analyzer_gui.py .\Ceiling_fan.sub
```

## Supported Input Format

The app is designed for Flipper Zero `RAW` Sub-GHz files that look like this:

```text
Filetype: Flipper SubGhz RAW File
Version: 1
Frequency: 433920000
Preset: FuriHalSubGhzPresetOok650Async
Protocol: RAW
RAW_Data: 413 -449 361 -420 ...
```

The loader:

- preserves non-empty header lines
- reads one or more `RAW_Data:` lines
- parses pulse durations as signed integers
- treats positive values as marks and negative values as spaces

When saving, the app rewrites the file with the original header lines and emits `RAW_Data:` in chunks.

## GUI Walkthrough

### Left Panel

- `Open .sub`
  Load a Flipper capture.

- `Save As...`
  Save an edited version of the currently loaded capture.

- `Gap threshold (us)`
  Controls frame splitting. Large negative spaces above this threshold are treated as boundaries between bursts/frames.

- `Decode mode`
  Switches how pulse timings are interpreted as bits.

- `Frames`
  Shows each detected frame with pulse count and approximate duration.

### Center Panel

- `Waterfall / Pulse Timing`
  A compact row-by-row visualization of the detected frames.

- `Zoom In / Zoom Out`
  Adjust horizontal pulse scaling.

- `Row selection`
  Click a row to:
  - select that frame
  - decode it in the right panel
  - highlight matching rows in the waterfall

- `Field rails`
  After structure analysis, colored rails appear above rows to mark inferred field regions.

### Right Panel

- `Decoder Panel`
  Shows metadata for the selected frame:
  - pulse count
  - decoded bit count
  - unknown pair count, when present
  - matching-row count, when a frame signature is highlighted
  - timing threshold

- `Bits`
  Editable bit view for the selected frame.

- `Hex bytes`
  Hex representation of the decoded bits.

- `Bits -> Hex`
  Converts the bit editor contents to hex.

- `Hex -> Bits`
  Converts the hex editor contents back to bits.

- `Redecode`
  Re-runs decode for the selected frame using the current mode.

- `Analyze`
  Performs cross-frame structure analysis and produces field guesses and protocol hints.

- `Apply Bits To Selected Frame`
  Rebuilds the selected frame’s pulse train from the edited bits using the current decode mode and inferred timing values.

## Decode Modes Explained

### `PWM space: short=0 long=1`

Treats each bit as a fixed mark followed by a space, where the space duration carries the bit value.

Best fit for many simple OOK/PWM remotes.

### `PWM pair: SS=0 SL=1`

Treats a short/short pair as `0` and short/long as `1`. Other combinations are marked unknown with `?`.

Useful when the protocol expresses information in paired mark/space timing.

### `Manchester: SL=1 LS=0`

Treats short/long and long/short pairs as logical states. Invalid combinations are emitted as `?`.

Useful when a capture looks transition-coded rather than pure pulse-width encoded.

### `Duration threshold`

Classifies each pulse directly by whether its absolute duration is above or below the inferred threshold.

This is a rough fallback mode and can still be useful when a signal does not fit the pair-based decoders cleanly.

## Structure Analysis

The `Analyze` action compares decoded frames of the dominant packet length and classifies each bit position as:

- `constant`
- `mostly constant`
- `variable`

From those spans, the app generates higher-level guesses such as:

- `preamble/sync candidate`
- `fixed header candidate`
- `serial/device ID candidate`
- `command/button/status candidate`
- `rolling code/auth block candidate`
- `CRC/checksum/status tail candidate`

It also provides heuristic hints based on:

- frequency band
- Flipper preset
- short/long timing clusters
- packet length
- how many bits stay fixed versus change across captures

This is intentionally heuristic analysis, not protocol-proof decoding. It is meant to speed up reverse-engineering, not replace validation.

## Recommended Workflow

For best results:

1. Capture the same remote/button multiple times.
2. Load one `.sub` file and check whether frame splitting looks sensible.
3. Try different decode modes until the decoded lengths and repeated-row matching look stable.
4. Run `Analyze` to identify fixed vs changing regions.
5. Compare captures from different buttons on the same remote.
6. Use the `Bits` and `Hex` panels to test small modifications.
7. Save edited output as a new `.sub` file rather than overwriting your original capture.

If your goal is protocol identification, a strong sign you are on the right path is:

- repeated frames align cleanly
- packet length stays consistent
- fixed header/ID sections remain stable
- only a small controlled region changes between buttons

## Included Sample Captures

The repository includes example captures around `433.92 MHz` using the Flipper preset `FuriHalSubGhzPresetOok650Async`, which is consistent with ASK/OOK-style analysis.

These are helpful for:

- verifying that the GUI launches correctly
- testing frame splitting and decode modes
- exploring the structure-analysis workflow without needing your own hardware capture first

## Limitations

- This tool targets `RAW` captures, not every possible decoded Flipper protocol format.
- Decode modes are heuristic and intentionally lightweight.
- Field labels such as `ID`, `rolling code`, or `CRC` are guesses based on bit behavior, not guaranteed identification.
- Rebuilt frames are useful for experimentation, but edited output may not reproduce a valid transmitter packet for every real protocol.
- Very noisy captures or poorly chosen gap thresholds can produce misleading frame boundaries.

## Safety and Legal Notes

Use this project only on signals and devices you own or are authorized to analyze.

Sub-GHz systems can include garage doors, alarms, vehicles, gates, fans, lights, and other access-related devices. Reverse engineering and retransmitting radio signals may be restricted by law, regulation, device terms, or local spectrum rules depending on where you are and what you are doing.

This software is best treated as a research and interoperability tool.

## Development Notes

The app is intentionally simple:

- one Python file
- standard-library only
- Tkinter UI
- straightforward `.sub` parsing and rewrite logic

That makes it easy to modify if you want to add:

- more decoder types
- export/import helpers
- better packet alignment
- CRC brute-force helpers
- side-by-side capture comparison
- protocol-specific plugins

## License

This project is licensed under the MIT License. See [LICENSE](/C:/Users/Joe/Projects/flipper-subghz-analyzer/LICENSE).

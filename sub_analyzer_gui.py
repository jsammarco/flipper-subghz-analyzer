import math
import os
import statistics
import sys
import tkinter as tk
from collections import Counter
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk


@dataclass
class Frame:
    start: int
    end: int
    gap_index: int | None


class SubFile:
    def __init__(self) -> None:
        self.path = ""
        self.header_lines: list[str] = []
        self.raw: list[int] = []

    def load(self, path: str) -> None:
        self.path = path
        self.header_lines = []
        self.raw = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("RAW_Data:"):
                    payload = stripped.split(":", 1)[1].strip()
                    if payload:
                        self.raw.extend(int(part) for part in payload.split())
                elif stripped:
                    self.header_lines.append(stripped)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for line in self.header_lines:
                fh.write(line + "\n")
            for idx in range(0, len(self.raw), 512):
                chunk = " ".join(str(v) for v in self.raw[idx : idx + 512])
                fh.write(f"RAW_Data: {chunk}\n")

    def header_value(self, key: str) -> str:
        prefix = key + ":"
        for line in self.header_lines:
            if line.startswith(prefix):
                return line.split(":", 1)[1].strip()
        return ""


def percentile(values: list[int], p: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(ordered[lo])
    return ordered[lo] * (hi - k) + ordered[hi] * (k - lo)


def split_frames(raw: list[int], gap_threshold: int) -> list[Frame]:
    frames: list[Frame] = []
    start = 0
    for idx, value in enumerate(raw):
        if value < 0 and abs(value) >= gap_threshold:
            if idx > start:
                frames.append(Frame(start, idx, idx))
            start = idx + 1
    if start < len(raw):
        frames.append(Frame(start, len(raw), None))
    return [frame for frame in frames if frame.end - frame.start >= 8]


def auto_gap_threshold(raw: list[int]) -> int:
    spaces = [abs(v) for v in raw if v < 0]
    if not spaces:
        return 10_000
    p95 = percentile(spaces, 0.95)
    return max(5_000, int(p95 * 0.55))


def cluster_timings(values: list[int]) -> tuple[int, int, int]:
    trimmed = [abs(v) for v in values if 80 <= abs(v) <= 5_000]
    if not trimmed:
        return (350, 750, 550)
    short_seed = percentile(trimmed, 0.35)
    long_seed = percentile(trimmed, 0.80)
    short_cluster: list[int] = []
    long_cluster: list[int] = []
    for value in trimmed:
        if abs(value - short_seed) <= abs(value - long_seed):
            short_cluster.append(value)
        else:
            long_cluster.append(value)
    short = int(statistics.median(short_cluster or trimmed))
    long = int(statistics.median(long_cluster or trimmed))
    if short > long:
        short, long = long, short
    threshold = (short + long) // 2
    return short, long, threshold


def bits_to_hex(bits: str) -> str:
    clean = "".join(ch for ch in bits if ch in "01")
    if not clean:
        return ""
    padded = clean + ("0" * ((8 - len(clean) % 8) % 8))
    return " ".join(f"{int(padded[i:i + 8], 2):02X}" for i in range(0, len(padded), 8))


def hex_to_bits(hex_text: str, bit_length: int | None = None) -> str:
    cleaned = "".join(ch for ch in hex_text if ch in "0123456789abcdefABCDEF")
    if len(cleaned) % 2:
        cleaned = "0" + cleaned
    bits = "".join(f"{int(cleaned[i:i + 2], 16):08b}" for i in range(0, len(cleaned), 2))
    if bit_length is not None and 0 < bit_length <= len(bits):
        return bits[:bit_length]
    return bits


def decode_frame(pulses: list[int], mode: str, threshold: int) -> str:
    bits: list[str] = []
    if mode == "Duration threshold":
        return "".join("1" if abs(v) >= threshold else "0" for v in pulses if abs(v) < 5_000)

    for idx in range(0, len(pulses) - 1, 2):
        mark = abs(pulses[idx])
        space = abs(pulses[idx + 1])
        m_long = mark >= threshold
        s_long = space >= threshold
        if mode == "PWM space: short=0 long=1":
            bits.append("1" if s_long else "0")
        elif mode == "PWM pair: SS=0 SL=1":
            if not m_long and not s_long:
                bits.append("0")
            elif not m_long and s_long:
                bits.append("1")
            else:
                bits.append("?")
        elif mode == "Manchester: SL=1 LS=0":
            if not m_long and s_long:
                bits.append("1")
            elif m_long and not s_long:
                bits.append("0")
            else:
                bits.append("?")
    return "".join(bits)


def build_pwm_frame(bits: str, short: int, long: int, mode: str) -> list[int]:
    clean = [ch for ch in bits if ch in "01"]
    out: list[int] = []
    for bit in clean:
        if mode in ("PWM space: short=0 long=1", "PWM pair: SS=0 SL=1"):
            out.extend([short, -(long if bit == "1" else short)])
        elif mode == "Manchester: SL=1 LS=0":
            if bit == "1":
                out.extend([short, -long])
            else:
                out.extend([long, -short])
        else:
            out.append(long if bit == "1" else short)
    return out


def build_threshold_frame(bits: str, original: list[int], short: int, long: int) -> list[int]:
    clean = [ch for ch in bits if ch in "01"]
    out: list[int] = []
    signs = [1 if pulse > 0 else -1 for pulse in original if abs(pulse) < 5_000]
    for idx, bit in enumerate(clean):
        sign = signs[idx] if idx < len(signs) else (1 if idx % 2 == 0 else -1)
        out.append(sign * (long if bit == "1" else short))
    return out


def decoded_bitstrings(frames: list[Frame], raw: list[int], mode: str, threshold: int) -> tuple[list[str], int]:
    by_length: Counter[int] = Counter()
    decoded: list[str] = []
    for frame in frames:
        bits = decode_frame(raw[frame.start : frame.end], mode, threshold)
        clean = "".join(ch for ch in bits if ch in "01")
        if len(clean) >= 16:
            decoded.append(clean)
            by_length[len(clean)] += 1
    if not by_length:
        return [], 0
    dominant_length, _ = by_length.most_common(1)[0]
    return [bits for bits in decoded if len(bits) == dominant_length], dominant_length


def run_lengths(mask: list[str]) -> list[tuple[int, int, str]]:
    if not mask:
        return []
    runs: list[tuple[int, int, str]] = []
    start = 0
    current = mask[0]
    for idx, value in enumerate(mask[1:], 1):
        if value != current:
            runs.append((start, idx - 1, current))
            start = idx
            current = value
    runs.append((start, len(mask) - 1, current))
    return runs


def smooth_classes(classes: list[str], min_run: int = 3) -> list[str]:
    if len(classes) < min_run * 2:
        return classes
    smoothed = classes[:]
    changed = True
    while changed:
        changed = False
        for start, end, kind in run_lengths(smoothed):
            width = end - start + 1
            if width >= min_run:
                continue
            left = smoothed[start - 1] if start > 0 else ""
            right = smoothed[end + 1] if end + 1 < len(smoothed) else ""
            replacement = ""
            if left and left == right:
                replacement = left
            elif "variable" in (left, right):
                replacement = "variable"
            elif "mostly constant" in (left, right):
                replacement = "mostly constant"
            elif left or right:
                replacement = left or right
            if replacement and replacement != kind:
                for idx in range(start, end + 1):
                    smoothed[idx] = replacement
                changed = True
                break
    return smoothed


def analyze_bit_structure(bitstrings: list[str]) -> tuple[list[tuple[int, int, str, float]], list[float]]:
    if not bitstrings:
        return [], []
    length = min(len(bits) for bits in bitstrings)
    sample_count = len(bitstrings)
    variability: list[float] = []
    classes: list[str] = []
    for idx in range(length):
        ones = sum(bits[idx] == "1" for bits in bitstrings)
        zeros = sample_count - ones
        change_ratio = min(ones, zeros) / sample_count
        variability.append(change_ratio)
        if change_ratio == 0:
            classes.append("constant")
        elif change_ratio <= 0.15:
            classes.append("mostly constant")
        else:
            classes.append("variable")
    classes = smooth_classes(classes)
    segments = []
    for start, end, kind in run_lengths(classes):
        avg = sum(variability[start : end + 1]) / (end - start + 1)
        segments.append((start, end, kind, avg))
    return segments, variability


def segment_label(start: int, end: int, kind: str, bit_length: int) -> str:
    width = end - start + 1
    tail = bit_length - end - 1
    if start == 0 and kind == "constant" and width >= 12:
        return "preamble/sync candidate"
    if start == 0 and kind == "constant":
        return "fixed header candidate"
    if tail <= 16 and kind != "constant" and width <= 16:
        return "CRC/checksum/status tail candidate"
    if tail <= 16 and kind == "constant" and width <= 16:
        return "fixed trailer/status candidate"
    if kind == "constant" and 20 <= width <= 40:
        return "serial/device ID candidate"
    if kind == "constant" and 12 <= width <= 56:
        return "fixed ID/header field candidate"
    if kind == "mostly constant" and width <= 12:
        return "command/button/status candidate"
    if kind == "mostly constant":
        return "mostly fixed field candidate"
    if kind == "variable" and width >= 24:
        return "rolling code/auth block candidate"
    if kind == "variable" and width >= 12:
        return "changing data field candidate"
    if kind == "variable":
        return "changing bits candidate"
    return "fixed field candidate"


def field_color(label: str) -> str:
    if "preamble" in label or "sync" in label:
        return "#f2cc60"
    if "serial" in label or "ID" in label or "header" in label:
        return "#7ee787"
    if "command" in label or "button" in label or "status" in label:
        return "#d2a8ff"
    if "rolling" in label or "auth" in label or "changing data" in label:
        return "#ff7b72"
    if "CRC" in label or "checksum" in label or "trailer" in label:
        return "#79c0ff"
    return "#8b949e"


def signal_hints(
    frequency: str,
    preset: str,
    mode: str,
    bit_length: int,
    sample_count: int,
    segments: list[tuple[int, int, str, float]],
    short: int,
    long: int,
) -> list[str]:
    hints: list[str] = []
    if "Ook" in preset or "ook" in preset.lower():
        hints.append("Physical layer: Flipper preset indicates ASK/OOK.")
    if frequency:
        try:
            mhz = int(frequency) / 1_000_000
            if 314 <= mhz <= 316:
                hints.append("Band: 315 MHz, common for North American automotive remotes and alarms.")
            elif 433 <= mhz <= 435:
                hints.append("Band: 433.92 MHz region, common for EU/Asia remotes, alarms, and sensors.")
            elif 867 <= mhz <= 869:
                hints.append("Band: 868 MHz ISM region, common in Europe.")
            elif 902 <= mhz <= 928:
                hints.append("Band: 902-928 MHz ISM region, common in North America.")
        except ValueError:
            pass
    if short and long:
        rough_rate = int(1_000_000 / max(1, short + long))
        hints.append(f"Timing: short/long clusters around {short}/{long} us, roughly {rough_rate} bps for PWM-style bits.")
    if "Manchester" in mode:
        hints.append("Encoding guess: Manchester-like, but verify with repeated aligned frames.")
    else:
        hints.append("Encoding guess: pulse-width/OOK-style, consistent with many fixed and rolling-code fobs.")

    variable_bits = sum(end - start + 1 for start, end, kind, _ in segments if kind == "variable")
    constant_bits = sum(end - start + 1 for start, end, kind, _ in segments if kind == "constant")
    if 60 <= bit_length <= 72 and variable_bits >= 24 and constant_bits >= 20:
        hints.append("Protocol-family candidate: KeeLoq/HCS-style rolling code is plausible, not proven.")
    elif variable_bits >= 64:
        hints.append("Protocol-family candidate: encrypted/authenticated rolling-code or AES-era packet is plausible.")
    elif sample_count >= 2 and variable_bits == 0:
        hints.append("Replay behavior hint: decoded frames are identical at this length, which looks fixed-code or repeated same burst.")
    if bit_length:
        hints.append(f"Dominant decoded packet length: {bit_length} bits across {sample_count} aligned frames.")
    return hints


class SubAnalyzerApp(tk.Tk):
    MODES = (
        "PWM space: short=0 long=1",
        "PWM pair: SS=0 SL=1",
        "Manchester: SL=1 LS=0",
        "Duration threshold",
    )

    def __init__(self) -> None:
        super().__init__()
        self.title("Flipper Sub-GHz RAW Analyzer")
        self.geometry("1180x760")
        self.minsize(980, 620)

        self.sub = SubFile()
        self.frames: list[Frame] = []
        self.short = 350
        self.long = 750
        self.threshold = 550
        self.current_frame = 0
        self.current_bit_length = 0
        self.structure_segments: list[tuple[int, int, str, float, str]] = []
        self.structure_bit_length = 0
        self.visible_row_bounds: list[tuple[int, int, int]] = []
        self.row_h = 22
        self.waterfall_top = 34
        self.frame_signatures: list[tuple[str, tuple[int, ...]] | None] = []
        self.highlight_signature: tuple[str, tuple[int, ...]] | None = None
        self._syncing_text = False

        self._build_ui()
        self._wire_events()

    def _build_ui(self) -> None:
        root = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(root, padding=10)
        center = ttk.Frame(root, padding=(0, 10))
        right = ttk.Frame(root, padding=10)
        root.add(left, weight=0)
        root.add(center, weight=1)
        root.add(right, weight=0)

        ttk.Button(left, text="Open .sub", command=self.open_file).pack(fill=tk.X)
        ttk.Button(left, text="Save As...", command=self.save_as).pack(fill=tk.X, pady=(6, 12))

        self.info = tk.StringVar(value="Open a Flipper RAW .sub file.")
        ttk.Label(left, textvariable=self.info, justify=tk.LEFT, wraplength=230).pack(fill=tk.X)

        ttk.Separator(left).pack(fill=tk.X, pady=12)
        ttk.Label(left, text="Gap threshold (us)").pack(anchor=tk.W)
        self.gap_var = tk.IntVar(value=10_000)
        gap = ttk.Scale(left, from_=1_000, to=80_000, orient=tk.HORIZONTAL, command=self._gap_changed)
        gap.configure(variable=self.gap_var)
        gap.pack(fill=tk.X)
        self.gap_label = tk.StringVar(value="10000")
        ttk.Label(left, textvariable=self.gap_label).pack(anchor=tk.W)

        ttk.Label(left, text="Decode mode").pack(anchor=tk.W, pady=(12, 0))
        self.mode_var = tk.StringVar(value=self.MODES[0])
        ttk.Combobox(left, textvariable=self.mode_var, values=self.MODES, state="readonly").pack(fill=tk.X)

        ttk.Label(left, text="Frames").pack(anchor=tk.W, pady=(12, 0))
        self.frame_list = tk.Listbox(left, height=18, exportselection=False)
        self.frame_list.pack(fill=tk.BOTH, expand=True)

        ttk.Label(center, text="Waterfall / Pulse Timing").pack(anchor=tk.W, padx=10)
        waterfall_frame = ttk.Frame(center)
        waterfall_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 0))
        self.canvas = tk.Canvas(waterfall_frame, bg="#111318", highlightthickness=0)
        self.waterfall_scrollbar = ttk.Scrollbar(waterfall_frame, orient=tk.VERTICAL, command=self._waterfall_yview)
        self.canvas.configure(yscrollcommand=self.waterfall_scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.waterfall_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tools = ttk.Frame(center)
        tools.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(tools, text="Zoom In", command=lambda: self._zoom(1.25)).pack(side=tk.LEFT)
        ttk.Button(tools, text="Zoom Out", command=lambda: self._zoom(0.8)).pack(side=tk.LEFT, padx=6)
        ttk.Button(tools, text="Apply Bits To Selected Frame", command=self.apply_bits).pack(side=tk.RIGHT)
        self.pixels_per_us = 0.018

        ttk.Label(right, text="Decoder Panel").pack(anchor=tk.W)
        self.decode_meta = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.decode_meta, justify=tk.LEFT, wraplength=330).pack(fill=tk.X, pady=(4, 8))

        ttk.Label(right, text="Bits").pack(anchor=tk.W)
        self.bits_text = tk.Text(right, height=13, width=46, wrap=tk.CHAR, undo=True)
        self.bits_text.pack(fill=tk.BOTH, expand=True)

        ttk.Label(right, text="Hex bytes").pack(anchor=tk.W, pady=(10, 0))
        self.hex_text = tk.Text(right, height=7, width=46, wrap=tk.WORD, undo=True)
        self.hex_text.pack(fill=tk.X)

        actions = ttk.Frame(right)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="Bits -> Hex", command=self.bits_to_hex_panel).pack(side=tk.LEFT)
        ttk.Button(actions, text="Hex -> Bits", command=self.hex_to_bits_panel).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Redecode", command=self.refresh_selected).pack(side=tk.RIGHT)

        structure_actions = ttk.Frame(right)
        structure_actions.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(structure_actions, text="Signal Structure").pack(side=tk.LEFT)
        ttk.Button(structure_actions, text="Analyze", command=self.analyze_structure).pack(side=tk.RIGHT)
        self.structure_text = tk.Text(right, height=13, width=46, wrap=tk.WORD)
        self.structure_text.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.structure_text.insert(
            "1.0",
            "Open a capture and click Analyze to compare decoded frames for fixed, changing, and high-entropy regions.",
        )

    def _wire_events(self) -> None:
        self.frame_list.bind("<<ListboxSelect>>", self._frame_selected)
        self.mode_var.trace_add("write", lambda *_: self._mode_changed())
        self.canvas.bind("<Configure>", lambda _: self.draw_waterfall())
        self.canvas.bind("<Button-1>", self._waterfall_clicked)
        self.canvas.bind("<MouseWheel>", self._waterfall_mousewheel)

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=os.getcwd(),
            title="Open Flipper .sub",
            filetypes=[("Flipper SUB files", "*.sub"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.sub.load(path)
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))
            return
        self.gap_var.set(auto_gap_threshold(self.sub.raw))
        self.analyze()

    def save_as(self) -> None:
        if not self.sub.raw:
            messagebox.showinfo("Nothing to save", "Open a .sub file first.")
            return
        initial = os.path.splitext(os.path.basename(self.sub.path or "edited.sub"))[0] + "_edited.sub"
        path = filedialog.asksaveasfilename(
            initialdir=os.path.dirname(self.sub.path) or os.getcwd(),
            initialfile=initial,
            defaultextension=".sub",
            filetypes=[("Flipper SUB files", "*.sub"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self.sub.save(path)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        messagebox.showinfo("Saved", f"Wrote {path}")

    def analyze(self) -> None:
        self.frames = split_frames(self.sub.raw, self.gap_var.get())
        self.structure_segments = []
        self.structure_bit_length = 0
        self.highlight_signature = None
        frame_values = [v for frame in self.frames for v in self.sub.raw[frame.start : frame.end]]
        self.short, self.long, self.threshold = cluster_timings(frame_values)
        self._rebuild_frame_signatures()

        self.frame_list.delete(0, tk.END)
        for index, frame in enumerate(self.frames):
            duration = sum(abs(v) for v in self.sub.raw[frame.start : frame.end])
            self.frame_list.insert(tk.END, f"{index:03d}  {frame.end - frame.start:4d} pulses  {duration / 1000:.1f} ms")
        if self.frames:
            self.current_frame = min(self.current_frame, len(self.frames) - 1)
            self.frame_list.selection_set(self.current_frame)
        self.info.set(
            f"{os.path.basename(self.sub.path)}\n"
            f"Frequency: {self.sub.header_value('Frequency') or '?'}\n"
            f"Preset: {self.sub.header_value('Preset') or '?'}\n"
            f"Pulses: {len(self.sub.raw):,}\n"
            f"Frames: {len(self.frames):,}\n"
            f"Short/long: {self.short}/{self.long} us"
        )
        self.gap_label.set(str(self.gap_var.get()))
        self.draw_waterfall()
        self.refresh_selected()

    def draw_waterfall(self) -> None:
        self.canvas.delete("all")
        self.visible_row_bounds = []
        if not self.frames:
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            return
        width = max(1, self.canvas.winfo_width())
        viewport_top = self.canvas.canvasy(0)
        viewport_bottom = viewport_top + max(1, self.canvas.winfo_height())
        first_row = max(0, int((viewport_top - self.waterfall_top) // self.row_h) - 1)
        last_row = min(
            len(self.frames),
            int((max(viewport_bottom - self.waterfall_top, 0)) // self.row_h) + 2,
        )
        self.canvas.create_text(
            8,
            8,
            text="click a row to select it and highlight matches; amber=mark, blue=space; colored rails show analyzed fields",
            fill="#c9d1d9",
            anchor=tk.W,
        )
        self._draw_field_legend(width)

        for frame_index in range(first_row, last_row):
            frame = self.frames[frame_index]
            y = self.waterfall_top + frame_index * self.row_h + 4
            x = 58
            is_selected = frame_index == self.current_frame
            is_match = self.highlight_signature is not None and self.frame_signatures[frame_index] == self.highlight_signature
            row_fill = "#18253a" if is_selected else "#13202b" if is_match else ""
            tag_fill = "#2f81f7" if is_selected else "#3fb950" if is_match else "#30363d"
            self.visible_row_bounds.append((frame_index, y - 8, y + 13))
            self.canvas.create_rectangle(0, y - 8, width, y + 13, fill=row_fill, outline="")
            self._draw_frame_field_markers(frame, x, y, width)
            label_color = "#ffffff" if is_selected else "#e3f2fd" if is_match else "#c9d1d9"
            self.canvas.create_text(6, y + 3, text=f"{frame_index:03d}", fill=label_color, anchor=tk.W)
            self.canvas.create_rectangle(50, y - 3, width - 8, y + 11, fill="#161b22", outline=tag_fill)
            for pulse in self.sub.raw[frame.start : frame.end]:
                dur = abs(pulse)
                bar_w = max(1, min(120, dur * self.pixels_per_us))
                color = "#f2a65a" if pulse > 0 else "#58a6ff"
                self.canvas.create_rectangle(x, y, min(x + bar_w, width - 9), y + 8, fill=color, outline="")
                x += bar_w
                if x > width - 10:
                    break
        total_height = self.waterfall_top + len(self.frames) * self.row_h + 8
        self.canvas.configure(scrollregion=(0, 0, width, total_height))

    def _draw_field_legend(self, width: int) -> None:
        if not self.structure_segments:
            return
        legend_items = [
            ("sync", "#f2cc60"),
            ("ID/header", "#7ee787"),
            ("cmd/status", "#d2a8ff"),
            ("rolling/auth", "#ff7b72"),
            ("CRC/tail", "#79c0ff"),
        ]
        x = 8
        y = 23
        for label, color in legend_items:
            self.canvas.create_rectangle(x, y - 5, x + 10, y + 5, fill=color, outline="")
            self.canvas.create_text(x + 14, y, text=label, fill="#8b949e", anchor=tk.W)
            x += 86
            if x > width - 90:
                break

    def _bit_to_pulse_index(self, bit_index: int) -> int:
        if self.mode_var.get() == "Duration threshold":
            return bit_index
        return bit_index * 2

    def _pulse_x_at(self, pulses: list[int], pulse_index: int, start_x: int) -> float:
        x = float(start_x)
        for pulse in pulses[: max(0, min(pulse_index, len(pulses)))]:
            x += max(1, min(120, abs(pulse) * self.pixels_per_us))
        return x

    def _draw_frame_field_markers(self, frame: Frame, start_x: int, y: int, width: int) -> None:
        if not self.structure_segments:
            return
        pulses = self.sub.raw[frame.start : frame.end]
        for start, end, _kind, _avg, label in self.structure_segments:
            color = field_color(label)
            x1 = 36 + (start / max(1, self.structure_bit_length)) * 12
            x2 = 36 + ((end + 1) / max(1, self.structure_bit_length)) * 12
            self.canvas.create_rectangle(x1, y - 7, max(x1 + 1, x2), y + 11, fill=color, outline="")
        for start, end, _kind, _avg, label in self.structure_segments:
            pulse_start = self._bit_to_pulse_index(start)
            pulse_end = self._bit_to_pulse_index(end + 1)
            x1 = self._pulse_x_at(pulses, pulse_start, start_x)
            x2 = self._pulse_x_at(pulses, pulse_end, start_x)
            if x1 >= width - 8:
                continue
            color = field_color(label)
            self.canvas.create_rectangle(max(start_x, x1), y - 7, min(width - 8, x2), y - 4, fill=color, outline="")

    def refresh_selected(self) -> None:
        if not self.frames:
            return
        selection = self.frame_list.curselection()
        if selection:
            self.current_frame = int(selection[0])
        frame = self.frames[self.current_frame]
        pulses = self.sub.raw[frame.start : frame.end]
        bits = decode_frame(pulses, self.mode_var.get(), self.threshold)
        clean_bits = "".join(ch for ch in bits if ch in "01")
        self.current_bit_length = len(clean_bits)
        unknowns = bits.count("?")
        self._syncing_text = True
        self.bits_text.delete("1.0", tk.END)
        self.bits_text.insert("1.0", bits)
        self.hex_text.delete("1.0", tk.END)
        self.hex_text.insert("1.0", bits_to_hex(clean_bits))
        self._syncing_text = False
        self.decode_meta.set(
            f"Frame {self.current_frame}\n"
            f"Pulses: {len(pulses)}\n"
            f"Decoded bits: {len(clean_bits)}"
            + (f"\nUnknown pairs: {unknowns}" if unknowns else "")
            + (f"\nMatching rows: {self._matching_frame_count()}" if self.highlight_signature is not None else "")
            + f"\nTiming threshold: {self.threshold} us"
        )
        self._scroll_current_frame_into_view()
        self.draw_waterfall()

    def analyze_structure(self) -> None:
        if not self.frames:
            messagebox.showinfo("No capture", "Open a .sub file first.")
            return
        bitstrings, bit_length = decoded_bitstrings(self.frames, self.sub.raw, self.mode_var.get(), self.threshold)
        self.structure_text.delete("1.0", tk.END)
        self.structure_segments = []
        self.structure_bit_length = 0
        if not bitstrings:
            self.structure_text.insert("1.0", "No stable decoded packets were found with the current mode and gap threshold.")
            self.draw_waterfall()
            return

        segments, variability = analyze_bit_structure(bitstrings)
        self.structure_segments = [
            (start, end, kind, avg_change, segment_label(start, end, kind, bit_length))
            for start, end, kind, avg_change in segments
        ]
        self.structure_bit_length = bit_length
        unique_packets = len(set(bitstrings))
        first_packet = bitstrings[0]
        variable_bits = sum(end - start + 1 for start, end, kind, _ in segments if kind == "variable")
        constant_bits = sum(end - start + 1 for start, end, kind, _ in segments if kind == "constant")
        mostly_constant_bits = sum(end - start + 1 for start, end, kind, _ in segments if kind == "mostly constant")
        lines = [
            "Structure analysis",
            f"Mode: {self.mode_var.get()}",
            f"Aligned frames: {len(bitstrings)}",
            f"Unique packets: {unique_packets}",
            f"Dominant length: {bit_length} bits",
            f"Fixed / mostly fixed / variable: {constant_bits} / {mostly_constant_bits} / {variable_bits} bits",
            "",
            "Field candidates:",
        ]

        for start, end, kind, avg_change, label in self.structure_segments:
            width = end - start + 1
            color = field_color(label)
            preview = first_packet[start : end + 1]
            if len(preview) > 32:
                preview = preview[:29] + "..."
            lines.append(
                f"{start:03d}-{end:03d} ({width:02d}b)  {kind}, change={avg_change:.2f}, color={color}  -> {label}"
            )
            lines.append(f"    sample: {preview}")

        changing_positions = [idx for idx, ratio in enumerate(variability) if ratio > 0]
        if changing_positions:
            lines.extend(
                [
                    "",
                    f"Changing bit positions: {changing_positions[:80]}"
                    + (" ..." if len(changing_positions) > 80 else ""),
                ]
            )

        lines.extend(["", "Signal hints:"])
        lines.extend(
            "- " + hint
            for hint in signal_hints(
                self.sub.header_value("Frequency"),
                self.sub.header_value("Preset"),
                self.mode_var.get(),
                bit_length,
                len(bitstrings),
                segments,
                self.short,
                self.long,
            )
        )
        lines.extend(
            [
                "",
                "Best workflow: capture many presses of the same button, then captures of other buttons. Constant spans are usually header/ID; small controlled changes are command/status; large changing spans are rolling/auth data. CRC/checksum guesses are strongest when the changing span sits at the packet tail and has an 8 or 16 bit width.",
            ]
        )
        self.structure_text.insert("1.0", "\n".join(lines))
        self.draw_waterfall()

    def bits_to_hex_panel(self) -> None:
        bits = self.bits_text.get("1.0", tk.END)
        self.current_bit_length = len("".join(ch for ch in bits if ch in "01"))
        self.hex_text.delete("1.0", tk.END)
        self.hex_text.insert("1.0", bits_to_hex(bits))

    def hex_to_bits_panel(self) -> None:
        hex_text = self.hex_text.get("1.0", tk.END)
        hex_digits = "".join(ch for ch in hex_text if ch in "0123456789abcdefABCDEF")
        byte_count = (len(hex_digits) + 1) // 2
        expected_byte_count = (self.current_bit_length + 7) // 8 if self.current_bit_length else 0
        bit_length = self.current_bit_length if byte_count == expected_byte_count else None
        bits = hex_to_bits(hex_text, bit_length)
        self.current_bit_length = len(bits)
        self.bits_text.delete("1.0", tk.END)
        self.bits_text.insert("1.0", bits)

    def apply_bits(self) -> None:
        if not self.frames:
            return
        mode = self.mode_var.get()
        bits = self.bits_text.get("1.0", tk.END)
        clean = "".join(ch for ch in bits if ch in "01")
        if not clean:
            messagebox.showinfo("No bits", "Enter or decode bits before applying.")
            return
        frame = self.frames[self.current_frame]
        original = self.sub.raw[frame.start : frame.end]
        if mode == "Duration threshold":
            replacement = build_threshold_frame(clean, original, self.short, self.long)
        else:
            replacement = build_pwm_frame(clean, self.short, self.long, mode)
        if not replacement:
            messagebox.showerror("Unsupported", "Could not build a replacement pulse train for this mode.")
            return
        self.sub.raw[frame.start : frame.end] = replacement
        self.current_frame = min(self.current_frame, len(self.frames) - 1)
        self.analyze()

    def _gap_changed(self, _: str) -> None:
        self.gap_label.set(str(self.gap_var.get()))
        if self.sub.raw:
            self.analyze()

    def _frame_selected(self, _: tk.Event) -> None:
        self.refresh_selected()

    def _mode_changed(self) -> None:
        self.structure_segments = []
        self.structure_bit_length = 0
        self.highlight_signature = None
        self._rebuild_frame_signatures()
        self.refresh_selected()

    def _waterfall_clicked(self, event: tk.Event) -> None:
        clicked_y = self.canvas.canvasy(event.y)
        for frame_index, top, bottom in self.visible_row_bounds:
            if top <= clicked_y <= bottom:
                self.current_frame = frame_index
                self.highlight_signature = self.frame_signatures[frame_index]
                self.frame_list.selection_clear(0, tk.END)
                self.frame_list.selection_set(frame_index)
                self.frame_list.see(frame_index)
                self.refresh_selected()
                return

    def _zoom(self, factor: float) -> None:
        self.pixels_per_us = min(0.12, max(0.002, self.pixels_per_us * factor))
        self.draw_waterfall()

    def _waterfall_mousewheel(self, event: tk.Event) -> str:
        if not self.frames:
            return "break"
        step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")
        self.draw_waterfall()
        return "break"

    def _waterfall_yview(self, *args: str) -> None:
        self.canvas.yview(*args)
        self.draw_waterfall()

    def _frame_signature(self, frame: Frame) -> tuple[str, tuple[int, ...]] | None:
        pulses = self.sub.raw[frame.start : frame.end]
        bits = decode_frame(pulses, self.mode_var.get(), self.threshold)
        clean_bits = "".join(ch for ch in bits if ch in "01")
        if clean_bits:
            return ("bits", tuple(int(ch) for ch in clean_bits))
        if pulses:
            return ("pulses", tuple(pulses))
        return None

    def _rebuild_frame_signatures(self) -> None:
        self.frame_signatures = [self._frame_signature(frame) for frame in self.frames]

    def _matching_frame_count(self) -> int:
        if self.highlight_signature is None:
            return 0
        return sum(1 for signature in self.frame_signatures if signature == self.highlight_signature)

    def _scroll_current_frame_into_view(self) -> None:
        if not self.frames:
            return
        total_height = self.waterfall_top + len(self.frames) * self.row_h + 8
        viewport_height = max(1, self.canvas.winfo_height())
        row_top = self.waterfall_top + self.current_frame * self.row_h
        row_bottom = row_top + self.row_h
        view_top = self.canvas.canvasy(0)
        view_bottom = view_top + viewport_height
        if row_top >= view_top and row_bottom <= view_bottom:
            return
        max_offset = max(1, total_height - viewport_height)
        target = max(0, min(row_top - self.row_h, max_offset))
        self.canvas.yview_moveto(target / max(1, total_height))


if __name__ == "__main__":
    app = SubAnalyzerApp()
    if len(sys.argv) > 1:
        try:
            app.sub.load(sys.argv[1])
            app.gap_var.set(auto_gap_threshold(app.sub.raw))
            app.analyze()
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))
    app.mainloop()

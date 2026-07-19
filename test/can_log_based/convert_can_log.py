#!/usr/bin/env python3
"""Convert CAN captures from common logging tools into the test-fixture format.

The fixture format (see canlog_safety_tests.cpp and utils.cpp) is one frame
per line:

    (12.345) RX0 3A [8] CE 25 4F 2B 6C 91 BF FC

Supported input formats (auto-detected per line, so mixed/garbage lines are
skipped and counted):

  emulator     (12.345) RX0 3A [8] CE 25 ...          Battery-Emulator web-UI
  candump-l    (1674587734.727) can0 132#9383CDFF     candump -l / -L
  candump      can0  1DB  [8]  00 04 C2 ...           candump (readable), with
               (1727438336.106) can0 0CF [08] B3 ...  or without timestamp
  savvycan     Time Stamp,ID,Extended,Dir,Bus,LEN,D1  SavvyCAN CSV export
  canhacker    ParserFlags<TAB>Ch<TAB>...             CANHacker/TSV export
  vector-asc   0.000000 1 502 Rx d 8 00 11 ...        Vector .asc

Frames whose direction is transmit (TX*, Tx) are dropped by default, since
fixtures must contain only frames the battery sent to the emulator.

Usage:
    convert_can_log.py INPUT... [-o OUTPUT] [--iface IFACE] [--keep-tx]
                       [--max-frames N] [--head]

With --max-frames the LAST N frames are kept (state at end of log best
matches a full replay); use --head to keep the first N instead.
"""

import argparse
import re
import sys

# (regex, handler) pairs tried in order per line. Handlers return
# (timestamp_or_None, direction, can_id, [bytes]) or None to reject.

RE_EMULATOR = re.compile(
    r"^\s*\((?P<ts>[\d.]+)\)\s+(?P<dir>[RT]X\d*)\s+(?P<id>[0-9A-Fa-f]+)"
    r"\s+\[(?P<dlc>\d+)\]\s+(?P<data>([0-9A-Fa-f]{1,2}\s*)*)$")
RE_CANDUMP_L = re.compile(
    r"^\s*\((?P<ts>[\d.]+)\)\s+(?P<iface>\S+)\s+(?P<id>[0-9A-Fa-f]+)#(?P<data>[0-9A-Fa-f]*)$")
RE_CANDUMP = re.compile(
    r"^\s*(\((?P<ts>[\d.]+)\)\s+)?(?P<iface>can\d+|vcan\d+|slcan\d+)\s+(?P<id>[0-9A-Fa-f]+)"
    r"\s+\[(?P<dlc>\d+)\]\s+(?P<data>([0-9A-Fa-f]{1,2}\s*)*)$")
RE_SAVVYCAN = re.compile(
    r"^\s*(?P<ts>-?\d+),(?P<id>[0-9A-Fa-f]+),(?P<ext>true|false),(?P<dir>Rx|Tx),"
    r"(?P<bus>\d+),(?P<len>\d+),(?P<data>.*)$", re.IGNORECASE)
RE_CANHACKER = re.compile(
    r"^\s*\d+\t\d+\t\d+\t(?P<dir>RX|TX)\t(?P<ts>\d+)\t[^\t]*\t"
    r"0x(?P<id>[0-9A-Fa-f]+)\t(?P<dlc>\d+)\t(?P<data>[0-9A-Fa-fx ]+)")
RE_VECTOR_ASC = re.compile(
    r"^\s*(?P<ts>[\d.]+)\s+\d+\s+(?P<id>[0-9A-Fa-f]+)x?\s+(?P<dir>Rx|Tx)\s+d\s+"
    r"(?P<dlc>\d+)\s+(?P<data>([0-9A-Fa-f]{1,2}\s*)*)$")


def parse_line(line):
    m = RE_EMULATOR.match(line)
    if m:
        data = m.group("data").split()
        if len(data) != int(m.group("dlc")):
            return None
        return (float(m.group("ts")), m.group("dir"), int(m.group("id"), 16),
                [int(b, 16) for b in data])

    m = RE_CANDUMP_L.match(line)
    if m:
        hexdata = m.group("data")
        if len(hexdata) % 2:
            return None
        data = [int(hexdata[i:i + 2], 16) for i in range(0, len(hexdata), 2)]
        return (float(m.group("ts")), "RX0", int(m.group("id"), 16), data)

    m = RE_CANDUMP.match(line)
    if m:
        data = m.group("data").split()
        if len(data) != int(m.group("dlc")):
            return None
        ts = float(m.group("ts")) if m.group("ts") else None
        return (ts, "RX0", int(m.group("id"), 16), [int(b, 16) for b in data])

    m = RE_SAVVYCAN.match(line)
    if m:
        length = int(m.group("len"))
        data = [b for b in m.group("data").rstrip(",").split(",") if b][:length]
        if len(data) != length:
            return None
        # SavvyCAN timestamps are microseconds (sometimes negative epoch-based)
        return (int(m.group("ts")) / 1e6, m.group("dir").upper() + "0",
                int(m.group("id"), 16), [int(b, 16) for b in data])

    m = RE_CANHACKER.match(line)
    if m:
        data = [b for b in m.group("data").replace("0x", "").split() if b]
        if len(data) != int(m.group("dlc")):
            return None
        return (int(m.group("ts")) / 1e3, m.group("dir") + "0",
                int(m.group("id"), 16), [int(b, 16) for b in data])

    m = RE_VECTOR_ASC.match(line)
    if m:
        data = m.group("data").split()
        if len(data) != int(m.group("dlc")):
            return None
        return (float(m.group("ts")), m.group("dir").upper() + "0",
                int(m.group("id"), 16), [int(b, 16) for b in data])

    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="input log file(s), concatenated in order")
    ap.add_argument("-o", "--output", help="output fixture path (default: stdout)")
    ap.add_argument("--iface", help="only keep frames from this interface token "
                    "(e.g. RX0 for emulator logs with multiple buses)")
    ap.add_argument("--keep-tx", action="store_true",
                    help="keep transmit-direction frames too")
    ap.add_argument("--drop-id", action="append", default=[],
                    help="drop frames with this hex CAN ID (repeatable); use for "
                    "tester/poller request IDs recorded as RX in bench captures")
    ap.add_argument("--max-frames", type=int,
                    help="keep at most N frames (the last N, unless --head)")
    ap.add_argument("--head", action="store_true",
                    help="with --max-frames, keep the first N frames instead")
    args = ap.parse_args()

    drop_ids = {int(i, 16) for i in args.drop_id}
    frames, skipped, dropped_tx = [], 0, 0
    for path in args.inputs:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip()[0] in "#;":
                    continue
                parsed = parse_line(line)
                if parsed is None:
                    skipped += 1
                    continue
                ts, direction, can_id, data = parsed
                if not args.keep_tx and direction.startswith("TX"):
                    dropped_tx += 1
                    continue
                if args.iface and direction != args.iface:
                    dropped_tx += 1
                    continue
                if can_id in drop_ids:
                    dropped_tx += 1
                    continue
                frames.append((ts, can_id, data))

    if args.max_frames and len(frames) > args.max_frames:
        frames = frames[:args.max_frames] if args.head else frames[-args.max_frames:]

    # Normalize timestamps to start near 0 (the parser reads but ignores them;
    # keeping them small and monotonic helps humans reading the fixture).
    base_ts = next((ts for ts, _, _ in frames if ts is not None), 0.0)
    out = open(args.output, "w") if args.output else sys.stdout
    synth_ts = 0.0
    for ts, can_id, data in frames:
        if ts is None:
            ts, synth_ts = synth_ts, synth_ts + 0.001
        else:
            ts -= base_ts
        out.write("(%.3f) RX0 %X [%d] %s\n" %
                  (ts, can_id, len(data), " ".join("%02X" % b for b in data)))
    if args.output:
        out.close()

    print("wrote %d frames (%d unparseable lines skipped, %d TX/other-iface dropped)"
          % (len(frames), skipped, dropped_tx), file=sys.stderr)
    return 0 if frames else 1


if __name__ == "__main__":
    sys.exit(main())

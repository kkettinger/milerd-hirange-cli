#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyserial",
# ]
# ///
"""
Milerd HiRange EMF Dosimeter – Live & Accumulated Dose Reader

Binary protocol over 230400 8N1 with SLIP framing.
"""

import sys
import time
import json
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError
import serial

DEVICE = "/dev/ttyACM0"

# ─── Protocol constants ─────────────────────────────────────────────────

MARKER = 0xC0
ESCAPE = 0xDB
ESC_MARKER = 0xDC
ESC_ESCAPE = 0xDD

REQ_HEADER = [0x00, 0x01, 0x00, 0x01]

# Command IDs (cmd_class, cmd_id)
CMD_GET_VERSION = [1, 1]
CMD_RESET = [1, 5]
CMD_GET_BOARD = [1, 10]
CMD_READ_PARAM = [1, 14]

# Parameter numbers
PARAM_ELECTRIC_LF = 260
PARAM_ELECTRIC_HF = 261
PARAM_MAGNETIC_LF = 262
PARAM_MAGNETIC_HF = 263
PARAM_RADIO_CONST = 264
PARAM_RADIO_PEAK = 265
PARAM_DOSE_PCT = 258
PARAM_POWER = 359

# (param, name, unit, convert_func or None)
EMF_CHANNELS = [
    (PARAM_RADIO_CONST,  "Radio Constant", "mW/m²"),
    (PARAM_RADIO_PEAK,   "Radio Peak",     "mW/m²"),
    (PARAM_ELECTRIC_LF,  "Electric LF",    "V/m"),
    (PARAM_ELECTRIC_HF,  "Electric HF",    "V/m"),
    (PARAM_MAGNETIC_LF,  "Magnetic LF",    "nT"),
    (PARAM_MAGNETIC_HF,  "Magnetic HF",    "nT"),
]

# Dose history: params 16-23, 4 bytes each → 32 days
DOSE_HISTORY_PARAMS = range(16, 24)

DOSE_LABELS = {
    0: "0%", 1: "10%", 2: "20%", 3: "30%", 4: "40%",
    5: "50%", 6: "60%", 7: "70%", 8: "0-79%",
    9: "80-99%", 10: "100-149%", 11: "150-199%", 12: "200%+",
}


def radio_to_mw_m2(raw):
    """Convert raw radio parameter to mW/m².

    Formula from MilerdPort app: 1000 * 10^((raw - 550) / 100).
    raw=0 gives ~0.003 which the app treats as zero (baseline noise floor),
    so we subtract it to match the displayed values.
    """
    BASELINE = 1000 * (10 ** (-550 / 100))  # ≈ 0.00316 → rounds to 0.003
    val = 1000 * (10 ** ((raw - 550) / 100)) - BASELINE
    return round(max(0.0, val), 3)


# ─── SLIP + CRC ─────────────────────────────────────────────────────────

def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc = (crc + 1 + (0x5A ^ b)) & 0xFFFF
    return crc


def slip_encode(data):
    out = []
    for b in data:
        if b == MARKER:
            out += [ESCAPE, ESC_MARKER]
        elif b == ESCAPE:
            out += [ESCAPE, ESC_ESCAPE]
        else:
            out.append(b)
    return out


def slip_decode(data):
    out = []
    i = 0
    while i < len(data):
        if data[i] == ESCAPE and i + 1 < len(data):
            out.append(MARKER if data[i + 1] == ESC_MARKER else
                       ESCAPE if data[i + 1] == ESC_ESCAPE else data[i])
            i += 2
        else:
            out.append(data[i])
            i += 1
    return out


def build_packet(payload):
    c = crc16(payload)
    encoded = slip_encode(list(payload) + [c & 0xFF, (c >> 8) & 0xFF])
    return bytes([MARKER] + encoded + [MARKER])


def parse_packet(raw):
    data = list(raw)
    try:
        start = data.index(MARKER)
        end = data.index(MARKER, start + 1)
    except ValueError:
        return None
    decoded = slip_decode(data[start + 1:end])
    if len(decoded) < 3:
        return None
    payload, rcrc = decoded[:-2], decoded[-2] | (decoded[-1] << 8)
    if rcrc != crc16(payload):
        return None
    return payload


# ─── Device communication ───────────────────────────────────────────────

class HiRange:
    def __init__(self, port=DEVICE):
        self.ser = serial.Serial(port, 230400, timeout=2)
        time.sleep(0.3)

    def close(self):
        self.ser.close()

    def _send(self, payload, timeout=2.0):
        self.ser.reset_input_buffer()
        self.ser.write(build_packet(bytes(payload)))
        raw = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                raw.extend(chunk)
                if raw.count(MARKER) >= 2:
                    r = parse_packet(raw)
                    if r is not None:
                        return r
            else:
                time.sleep(0.02)
        return parse_packet(raw) if raw else None

    def read_param(self, num):
        """Read a 16-bit parameter. Returns int value or None."""
        resp = self._send(REQ_HEADER + CMD_READ_PARAM + [num & 0xFF, (num >> 8) & 0xFF])
        if resp and len(resp) >= 10 and resp[4] == 0x41 and resp[5] == 14:
            return resp[8] | (resp[9] << 8)
        return None

    def read_param_32(self, num):
        """Read a parameter and return full 32-bit value from bytes 8-11."""
        resp = self._send(REQ_HEADER + CMD_READ_PARAM + [num & 0xFF, (num >> 8) & 0xFF])
        if resp and len(resp) >= 12 and resp[4] == 0x41 and resp[5] == 14:
            return resp[8] | (resp[9] << 8) | (resp[10] << 16) | (resp[11] << 24)
        return None

    def get_board_info(self):
        resp = self._send(REQ_HEADER + CMD_GET_BOARD)
        if resp and len(resp) >= 10 and resp[4] == 0x41 and resp[5] == 10:
            code = resp[6] | (resp[7] << 8)
            return {290: "HiRange", 278: "AeroQ"}.get(code, f"Unknown({code})"), resp[8]
        return None, None

    def get_firmware_version(self):
        resp = self._send(REQ_HEADER + CMD_GET_VERSION)
        if resp and len(resp) >= 14 and resp[4] == 0x41 and resp[5] == 1:
            app = resp[8] | (resp[9] << 8)
            return f"{(app >> 8) & 0xFF}.{app & 0xFF}.0" if app else "bootloader"
        return None

    def get_emf(self):
        """Read all 6 EMF channels. Returns list of (name, value, unit)."""
        result = []
        for num, name, unit in EMF_CHANNELS:
            raw = self.read_param(num)
            if raw is None:
                result.append((name, None, unit))
            elif num in (PARAM_RADIO_CONST, PARAM_RADIO_PEAK):
                result.append((name, radio_to_mw_m2(raw), unit))
            else:
                result.append((name, raw, unit))
        return result

    def get_dose_pct(self):
        """Read current accumulated dose percentage."""
        return self.read_param(PARAM_DOSE_PCT)

    def get_battery(self):
        """Read battery level. Returns (percent, usb_connected)."""
        val = self.read_param(PARAM_POWER)
        if val is None:
            return None, False
        level = val & 0x7F
        usb = (val & 0x80) != 0
        return level, usb

    def reset_device(self, mode=0):
        """Send reset command. mode: 0=simple reset, 2=restart in main mode."""
        self._send(REQ_HEADER + CMD_RESET + [mode, 0, 0, 0, 10])

    def get_dose_history(self):
        """Read 30-day dose history. Returns list of (day_offset, raw, label)."""
        history = []
        for param in DOSE_HISTORY_PARAMS:
            resp = self._send(REQ_HEADER + CMD_READ_PARAM + [param & 0xFF, (param >> 8) & 0xFF])
            if not resp or len(resp) < 12:
                continue
            if resp[4] != 0x41 or resp[5] != 14:
                continue
            for i in range(4):
                day = 4 * (param - 16) + i
                if day >= 30:
                    break
                b = resp[8 + i]
                history.append((day, b, DOSE_LABELS.get(b, f"?({b})")))
        return history


# ─── Output ─────────────────────────────────────────────────────────────

def print_header(dev):
    board, ver = dev.get_board_info()
    fw = dev.get_firmware_version()
    print(f"Device: {board}  Board rev: {ver}  Firmware: {fw}")
    print()


def print_live(dev):
    emf = dev.get_emf()
    dose = dev.get_dose_pct()
    batt_pct, usb = dev.get_battery()

    print("── Live readings ──────────────────────────────")
    for name, val, unit in emf:
        if val is None:
            print(f"  {name:<16}        - {unit}")
        elif isinstance(val, float):
            print(f"  {name:<16} {val:>8.3f} {unit}")
        else:
            print(f"  {name:<16} {val:>8} {unit}")
    print()
    print(f"  {'Current dose':<16} {dose}%")
    batt_str = f"{batt_pct}%" + (" (USB)" if usb else "")
    print(f"  {'Battery':<16} {batt_str}")
    print()


def print_history(dev):
    history = dev.get_dose_history()
    today = datetime.now().date()

    print("── 30-day accumulated dose history ────────────")
    print(f"  {'Day':>4}  {'Date':>12}  {'Value'}")
    print(f"  {'───':>4}  {'────':>12}  {'─────'}")
    for day, raw, label in history:
        d = today - timedelta(days=day + 1)
        print(f"  {day:>4}  {d.isoformat():>12}  {label}")
    print()


def live_loop(dev):
    """Continuously print live EMF + dose every second."""
    print("── Live monitor (Ctrl-C to stop) ──────────────")
    print()
    try:
        while True:
            emf = dev.get_emf()
            dose = dev.get_dose_pct()
            ts = datetime.now().strftime("%H:%M:%S")
            parts = [f"{n}: {v:>8.3f} {u}" if isinstance(v, float) else f"{n}: {v:>4} {u}" if v is not None else f"{n}:        - {u}" for n, v, u in emf]
            print(f"  [{ts}]  {' | '.join(parts)}  |  dose: {dose}%")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Stopped.")
        print()


FW_UPDATE_URL = "https://milerdport.cloud/api/firmware_updates/current?platform=Hirange&type=E"


def check_firmware_update(dev):
    """Check if a newer firmware is available online."""
    local_ver = dev.get_firmware_version()
    print(f"── Firmware update check ───────────────────────")
    print(f"  Installed: {local_ver}")
    try:
        req = Request(FW_UPDATE_URL, headers={"User-Agent": "MilerdReader/1.0"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        remote_ver = data.get("version", "?")
        print(f"  Latest:    {remote_ver}")
        if remote_ver == local_ver:
            print(f"  Firmware is up to date.")
        else:
            print(f"  Update available!")
    except (URLError, OSError, json.JSONDecodeError, KeyError) as e:
        print(f"  Could not check: {e}")
    print()


def main():
    import argparse
    p = argparse.ArgumentParser(description="Milerd HiRange EMF reader")
    p.add_argument("-d", "--device", default=DEVICE, help="serial port path")
    p.add_argument("-l", "--live", action="store_true", help="continuous live monitor")
    p.add_argument("-u", "--update-check", action="store_true", help="check for firmware updates")
    p.add_argument("--power-cycle", action="store_true", help="power cycle (reset) the device")
    args = p.parse_args()

    dev = HiRange(args.device)
    try:
        print_header(dev)
        if args.power_cycle:
            print("Power cycling device...")
            dev.reset_device(0)
            print("Reset command sent.")
        elif args.update_check:
            check_firmware_update(dev)
        elif args.live:
            live_loop(dev)
        else:
            print_live(dev)
            print_history(dev)
    finally:
        dev.close()


if __name__ == "__main__":
    main()

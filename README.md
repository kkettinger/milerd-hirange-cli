# milerd-hirange-cli

CLI tool for reading EMF measurements from the Milerd HiRange dosimeter over USB serial.

Reads all 6 EMF channels (radio, electric, magnetic), current dose percentage, battery level, and 30-day dose history.

## Usage

Requires [uv](https://docs.astral.sh/uv/). No install needed, dependencies are fetched automatically.

```
uv run hirange.py            # snapshot reading + dose history
uv run hirange.py --live     # continuous 1s updates (Ctrl-C to stop)
uv run hirange.py --device /dev/ttyACM1  # custom serial port
uv run hirange.py --update-check         # check for firmware updates
uv run hirange.py --power-cycle          # reset the device
```

## Example output

### Default (snapshot + history)

```sh
Device: HiRange  Board rev: 3  Firmware: 1.2.0

── Live readings ──────────────────────────────
  Radio Constant      0.001 mW/m²
  Radio Peak          0.226 mW/m²
  Electric LF           190 V/m
  Electric HF             7 V/m
  Magnetic LF             4 nT
  Magnetic HF             0 nT

  Current dose     10%
  Battery          21%

── 30-day accumulated dose history ────────────
   Day          Date  Value
   ───          ────  ─────
     0    2026-03-11  40%
     1    2026-03-10  40%
     2    2026-03-09  40%
   ...
```

### Live monitor (`--live`)

```sh
Device: HiRange  Board rev: 3  Firmware: 1.2.0

── Live monitor (Ctrl-C to stop) ──────────────

  [08:09:40]  Radio Constant:    0.001 mW/m² | Radio Peak:    0.187 mW/m² | Electric LF:  193 V/m | Electric HF:   10 V/m | Magnetic LF:    2 nT | Magnetic HF:    0 nT  |  dose: 10%
  [08:09:41]  Radio Constant:    0.000 mW/m² | Radio Peak:    0.005 mW/m² | Electric LF:  189 V/m | Electric HF:   10 V/m | Magnetic LF:    2 nT | Magnetic HF:    1 nT  |  dose: 10%
  [08:09:42]  Radio Constant:    0.001 mW/m² | Radio Peak:    0.423 mW/m² | Electric LF:  193 V/m | Electric HF:    9 V/m | Magnetic LF:    2 nT | Magnetic HF:    0 nT  |  dose: 10%
```

# CircuitPython Board Viewer

A static site that answers two questions that are otherwise hard to
answer about a CircuitPython board without reading its firmware
source: what pin names does it expose (`board.D0` vs `board.GP0`,
and which names alias the same physical pin), and does it define
`board.SPI()`, `board.I2C()`, `board.UART()`, or `board.DISPLAY` —
and if so, which pins actually back them.

[![View Boards](https://img.shields.io/badge/-View%20Boards-652f8f?style=for-the-badge)](https://todbot.github.io/CircuitPython_BoardViewer/)

Live site: [https://todbot.github.io/CircuitPython_BoardViewer/](https://todbot.github.io/CircuitPython_BoardViewer/)

## How it works

- `tools/gen_board_data.py` parses a local CircuitPython checkout's
  `pins.c` and `mpconfigboard.h` for every "classic layout" board
  (636 of ~660; zephyr-cp and silabs boards use different formats
  and are skipped for now) and writes `docs/board_data.json`.
- `docs/index.html` is a single self-contained page (no build step,
  no framework) that loads `board_data.json` and renders a board
  search/picker plus a pin table and board-object summary.
- `.github/workflows/update-board-data.yml` refreshes
  `docs/board_data.json` weekly from the live CircuitPython repo.

Note: `pins.c` is the only source of truth for what actually exists as a
Python attribute on `board`. In `mpconfigboard.h` file, enabling a bus does
not guarantee it's exposed, and a name being exposed does not
guarantee pins are configured for it. Real board definitions have
both kinds of mismatch, and the viewer shows both cases explicitly
(e.g. "defined, but no default pins configured — calling this may
raise `NotImplementedError`").

## Regenerating the data locally

```sh
python3 tools/gen_board_data.py --cp /path/to/circuitpython
```

Auto-discovers a checkout at `../circuitpython` or
`~/projects/adafruit/circuitpython*` if `--cp` is omitted.

## Running locally

```sh
cd docs && python3 -m http.server 8000
```

Then open `http://localhost:8000/`.

## Current limitations

- silabs (`pins.csv`) and zephyr-cp (`circuitpython.toml`) boards
  are not parsed — they use a different board-definition format.
- `board.DISPLAY` is shown as present/absent only; its size and
  driver chip live in board-specific C code and aren't extracted.
- Backing pins for non-standard board objects (e.g. `STEMMA_I2C`
  when it's a separate bus from `I2C`, `SD_SPI`) usually aren't
  extractable, since they're wired up in board-specific C code with
  no fixed macro to parse. When a board defines more pin-structs in
  `mpconfigboard.h` than it has standard buses (e.g.
  `CIRCUITPY_BOARD_I2C_PIN` with two entries but only one `I2C`),
  the viewer makes a best-effort guess pairing the extra struct with
  the extra bus-like object name (by position, in `pins.c` order) —
  shown with a "(guess)" label since there's no macro confirming the
  pairing is correct.
- No pinout diagrams — text/table only.  See the relevant Learn Guide.

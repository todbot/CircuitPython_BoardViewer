#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Generate docs/board_data.json: per-board pin names, aliases, and
board-object (I2C/SPI/UART/DISPLAY) availability, extracted from a
CircuitPython firmware checkout's pins.c / mpconfigboard.h files.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

CP_CANDIDATES = [
    "../circuitpython",
    "~/projects/adafruit/circuitpython-claudetest",
    "~/projects/adafruit/circuitpython-adafruit",
    "~/projects/adafruit/circuitpython",
]

STANDARD_BUSES = ("i2c", "spi", "uart")

BUS_FIELDS = {
    "i2c": {"scl": "DEFAULT_I2C_BUS_SCL", "sda": "DEFAULT_I2C_BUS_SDA"},
    "spi": {
        "sck": "DEFAULT_SPI_BUS_SCK",
        "mosi": "DEFAULT_SPI_BUS_MOSI",
        "miso": "DEFAULT_SPI_BUS_MISO",
    },
    "uart": {"tx": "DEFAULT_UART_BUS_TX", "rx": "DEFAULT_UART_BUS_RX"},
}

BUS_OVERRIDE_MACROS = {
    "i2c": ("CIRCUITPY_BOARD_I2C", "CIRCUITPY_BOARD_I2C_PIN"),
    "spi": ("CIRCUITPY_BOARD_SPI", "CIRCUITPY_BOARD_SPI_PIN"),
    "uart": ("CIRCUITPY_BOARD_UART", "CIRCUITPY_BOARD_UART_PIN"),
}

DISPLAY_VARIANTS = ("display", "epaper_display", "framebuffer_display")


def find_cp_root(explicit_path):
    candidates = [explicit_path] if explicit_path else CP_CANDIDATES
    for candidate in candidates:
        path = Path(candidate).expanduser().resolve()
        if (path / "docs" / "shared_bindings_matrix.py").exists():
            return path
    raise SystemExit(
        "Could not find a CircuitPython checkout. Pass --cp PATH.\n"
        f"Tried: {[str(Path(c).expanduser()) for c in candidates]}"
    )


def get_board_mapping(cp_root):
    sys.path.insert(0, str(cp_root / "docs"))
    from shared_bindings_matrix import get_board_mapping as _get_board_mapping

    return _get_board_mapping()


def parse_pins_c(path):
    """Classify each board_module_globals_table row in pins.c."""
    pins = defaultdict(list)  # mcu_pin -> [board names], in file order
    pin_order = []
    objects = defaultdict(list)  # singleton name -> [board names]
    displays = []  # list of {"index": int, "variant": str, "names": [...]}
    unmapped = []

    display_by_key = {}

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or "MP_QSTR" not in line:
            continue

        name_match = re.search(r"MP_ROM_QSTR\(MP_QSTR_(\w+)\),\s*MP_ROM_PTR", line)
        if name_match is None:
            name_match = re.search(r"MP_OBJ_NEW_QSTR\(MP_QSTR_(\w+)\),\s*MP_ROM_PTR", line)
        if name_match is None:
            continue
        name = name_match.group(1)

        pin_match = re.search(r"MP_ROM_PTR\(&pin_(\w+)\)", line)
        if pin_match:
            mcu_pin = pin_match.group(1)
            if mcu_pin not in pins:
                pin_order.append(mcu_pin)
            pins[mcu_pin].append(name)
            continue

        display_match = re.search(
            r"MP_ROM_PTR\(&displays\[(\d+)\]\.(" + "|".join(DISPLAY_VARIANTS) + r")\)", line
        )
        if display_match:
            index, variant = int(display_match.group(1)), display_match.group(2)
            key = (index, variant)
            if key not in display_by_key:
                display_by_key[key] = {"index": index, "variant": variant, "names": []}
                displays.append(display_by_key[key])
            display_by_key[key]["names"].append(name)
            continue

        obj_match = re.search(r"MP_ROM_PTR\(&(\w+)_obj\)", line)
        if obj_match:
            singleton = obj_match.group(1)
            if singleton.startswith("board_"):
                singleton = singleton[len("board_") :]
            objects[singleton].append(name)
            continue

        unmapped.append(stripped)

    pin_list = [{"mcu_pin": mcu_pin, "names": pins[mcu_pin]} for mcu_pin in pin_order]
    return pin_list, objects, displays, unmapped


def parse_bus_pins(header_text, bus):
    """Return a pins dict for the given bus (i2c/spi/uart), or None."""
    fields = BUS_FIELDS[bus]
    direct = {}
    for field, macro in fields.items():
        m = re.search(rf"#define\s+{macro}\s+\(&pin_(\w+)\)", header_text)
        if m:
            direct[field] = m.group(1)
    if len(direct) == len(fields):
        return direct

    flag_macro, pin_macro = BUS_OVERRIDE_MACROS[bus]
    flag_match = re.search(rf"#define\s+{flag_macro}\s+\((\d+)\)", header_text)
    pin_match = re.search(rf"#define\s+{pin_macro}\s+(.*)", header_text)
    if flag_match and int(flag_match.group(1)) > 0 and pin_match:
        structs = re.findall(r"\{[^{}]*\}", pin_match.group(1))
        for struct in structs:
            pins = dict(re.findall(r"\.(\w+)\s*=\s*&pin_(\w+)", struct))
            if pins:
                return pins
    return None


def join_line_continuations(text):
    """Collapse `...\\\n...` continuations so multi-line macros parse as one line."""
    return re.sub(r"\\\r?\n\s*", " ", text)


def parse_mpconfigboard_h(header_text):
    header_text = join_line_continuations(header_text)
    name_match = re.search(r'#define\s+MICROPY_HW_BOARD_NAME\s+"([^"]*)"', header_text)
    mcu_match = re.search(r'#define\s+MICROPY_HW_MCU_NAME\s+"([^"]*)"', header_text)
    buses = {bus: parse_bus_pins(header_text, bus) for bus in STANDARD_BUSES}
    return {
        "name": name_match.group(1) if name_match else None,
        "mcu": mcu_match.group(1) if mcu_match else None,
        "buses": buses,
    }


def build_board_record(board_id, info, discrepancy_counts):
    board_dir = info["directory"]
    pins_c = board_dir / "pins.c"
    mpconfigboard_h = board_dir / "mpconfigboard.h"

    pin_list, objects, displays, unmapped = parse_pins_c(pins_c)
    header_info = parse_mpconfigboard_h(mpconfigboard_h.read_text(encoding="utf-8"))

    # `pins.c` is the sole source of truth for what board.X actually exists as a
    # Python attribute (CIRCUITPYTHON_BOARD_DICT_STANDARD_ITEMS only injects
    # __name__/board_id). mpconfigboard.h enabling a bus does not guarantee
    # pins.c exposes it, and pins.c exposing a name does not guarantee
    # mpconfigboard.h configured pins for it. Both directions occur in real
    # board definitions, so surface both facts rather than reconciling them.
    std_objects = {}
    for bus in STANDARD_BUSES:
        available = bus in objects
        header_pins = header_info["buses"][bus]
        if not available and header_pins is None:
            std_objects[bus] = None
            continue
        note = None
        if available and header_pins is None:
            note = "no default pins configured; calling this may raise NotImplementedError"
            discrepancy_counts[f"{bus}_no_pins"] += 1
        elif not available and header_pins is not None:
            note = "pins configured in mpconfigboard.h but not exposed as board.X"
            discrepancy_counts[f"{bus}_not_exposed"] += 1
        std_objects[bus] = {
            "available": available,
            "names": objects.get(bus, []),
            "pins": header_pins,
            "note": note,
        }

    other_objects = [
        {"names": names, "singleton": f"board_{singleton}_obj", "pins": None}
        for singleton, names in objects.items()
        if singleton not in STANDARD_BUSES
    ]

    display = None
    if displays:
        primary = displays[0]
        display = {"names": primary["names"], "variant": primary["variant"]}

    return {
        "name": header_info["name"] or board_id,
        "mcu": header_info["mcu"],
        "port": info["port"],
        "aliases": info.get("aliases", []),
        "pins": pin_list,
        "objects": {**std_objects, "display": display},
        "other_objects": other_objects,
        "unmapped": unmapped,
    }


def get_cp_sha(cp_root):
    try:
        result = subprocess.run(
            ["git", "-C", str(cp_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except OSError:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cp", help="Path to a CircuitPython checkout")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent / "docs" / "board_data.json"),
        help="Output JSON path",
    )
    args = parser.parse_args()

    cp_root = find_cp_root(args.cp)
    print(f"Using CircuitPython checkout: {cp_root}", file=sys.stderr)

    board_mapping = get_board_mapping(cp_root)

    boards = {}
    skipped = {}
    discrepancy_counts = defaultdict(int)

    for board_id, info in board_mapping.items():
        if info.get("alias"):
            continue

        board_dir = info["directory"]
        if not (board_dir / "pins.c").exists() or not (board_dir / "mpconfigboard.h").exists():
            skipped[board_id] = {
                "port": info["port"],
                "reason": "non-classic layout (no pins.c/mpconfigboard.h)",
            }
            continue

        try:
            boards[board_id] = build_board_record(board_id, info, discrepancy_counts)
        except Exception as exc:  # noqa: BLE001 - keep generating other boards
            skipped[board_id] = {"port": info["port"], "reason": f"parse error: {exc}"}

    data = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cp_sha": get_cp_sha(cp_root),
        "boards": boards,
        "skipped": skipped,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Parsed {len(boards)} boards, skipped {len(skipped)}", file=sys.stderr)
    for key, count in sorted(discrepancy_counts.items()):
        if count:
            print(f"NOTE: {count} boards: {key}", file=sys.stderr)
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()

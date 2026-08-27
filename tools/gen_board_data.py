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


def parse_bus_pin_structs(header_text, bus):
    """Return every pin-struct defined for a bus (i2c/spi/uart).

    Usually just one. Boards with CIRCUITPY_BOARD_<BUS> > 1 define an
    array of structs — the first backs the standard board.<BUS>() and
    later ones typically back an extra non-standard singleton (e.g.
    STEMMA_I2C) exposed only in pins.c. There's no macro that names
    which extra singleton a given array position belongs to, so pairing
    them (done in build_board_record) is a positional guess.
    """
    fields = BUS_FIELDS[bus]
    direct = {}
    for field, macro in fields.items():
        m = re.search(rf"#define\s+{macro}\s+\(&pin_(\w+)\)", header_text)
        if m:
            direct[field] = m.group(1)
    if len(direct) == len(fields):
        return [direct]

    flag_macro, pin_macro = BUS_OVERRIDE_MACROS[bus]
    flag_match = re.search(rf"#define\s+{flag_macro}\s+\((\d+)\)", header_text)
    pin_match = re.search(rf"#define\s+{pin_macro}\s+(.*)", header_text)
    if flag_match and int(flag_match.group(1)) > 0 and pin_match:
        structs = re.findall(r"\{[^{}]*\}", pin_match.group(1))
        result = []
        for struct in structs:
            pins = dict(re.findall(r"\.(\w+)\s*=\s*&pin_(\w+)", struct))
            if pins:
                result.append(pins)
        return result
    return []


def join_line_continuations(text):
    """Collapse `...\\\n...` continuations so multi-line macros parse as one line."""
    return re.sub(r"\\\r?\n\s*", " ", text)


def strip_c_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def find_call_args(text, func_name):
    """Return the raw text inside the matching parens of a call to
    func_name(...), or None if the call isn't present."""
    m = re.search(re.escape(func_name) + r"\s*\(", text)
    if not m:
        return None
    depth = 1
    i = m.end()
    while i < len(text) and depth > 0:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        i += 1
    return text[m.end() : i - 1]


def split_top_level_args(arg_text):
    """Split a C call's argument text on commas, ignoring commas nested
    inside parens/brackets/braces (e.g. struct initializers)."""
    args = []
    depth = 0
    current = []
    for ch in arg_text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append("".join(current).strip())
    return args


def resolve_int(token, macros, _seen=frozenset()):
    """Resolve a C integer literal or #define'd macro name to an int.
    Returns None for anything more complex (expressions, unknown names)."""
    token = token.strip()
    m = re.match(r"^(0[xX][0-9a-fA-F]+|\d+)[uUlL]*$", token)
    if m:
        return int(m.group(1), 0)
    if token in macros and token not in _seen:
        return resolve_int(macros[token], macros, _seen | {token})
    return None


def parse_display_size(board_c_text, variant):
    """Extract width/height/color_depth for a board's primary display from
    board.c, for the two display driver families with a fixed, parseable
    call shape. Returns None if not extractable (e.g. framebuffer-backed
    displays, whose dimensions live on a per-driver-type constructor with
    no single common shape)."""
    text = strip_c_comments(board_c_text)
    macros = {k: v.strip() for k, v in re.findall(r"#define\s+(\w+)\s+([^\n]+)", text)}

    if variant == "display":
        args_text = find_call_args(text, "common_hal_busdisplay_busdisplay_construct")
        if args_text is None:
            return None
        args = split_top_level_args(args_text)
        if len(args) < 8:
            return None
        width = resolve_int(args[2], macros)
        height = resolve_int(args[3], macros)
        color_depth = resolve_int(args[7], macros)
        if width is None or height is None:
            return None
        return {"width": width, "height": height, "color_depth": color_depth}

    if variant == "epaper_display":
        w_match = re.search(r"args\.width\s*=\s*([^;]+);", text)
        h_match = re.search(r"args\.height\s*=\s*([^;]+);", text)
        if not w_match or not h_match:
            return None
        width = resolve_int(w_match.group(1), macros)
        height = resolve_int(h_match.group(1), macros)
        if width is None or height is None:
            return None
        return {"width": width, "height": height, "color_depth": None}

    return None


def parse_mpconfigboard_h(header_text):
    header_text = join_line_continuations(header_text)
    name_match = re.search(r'#define\s+MICROPY_HW_BOARD_NAME\s+"([^"]*)"', header_text)
    mcu_match = re.search(r'#define\s+MICROPY_HW_MCU_NAME\s+"([^"]*)"', header_text)
    bus_structs = {bus: parse_bus_pin_structs(header_text, bus) for bus in STANDARD_BUSES}
    buses = {bus: (structs[0] if structs else None) for bus, structs in bus_structs.items()}
    return {
        "name": name_match.group(1) if name_match else None,
        "mcu": mcu_match.group(1) if mcu_match else None,
        "buses": buses,
        "bus_structs": bus_structs,
    }


def guess_bus_kind(singleton):
    """Guess which standard bus a non-standard singleton is related to,
    from its name (e.g. "stemma_i2c" -> "i2c"). Returns None if no
    standard bus name appears in it."""
    lowered = singleton.lower()
    for bus in STANDARD_BUSES:
        if bus in lowered:
            return bus
    return None


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

    # Extra CIRCUITPY_BOARD_<BUS>_PIN array entries beyond the first (which
    # backs the standard board.<BUS>()) usually belong to one of these
    # non-standard singletons, in pins.c order — but nothing names which
    # array position goes with which singleton, so this pairing is a guess.
    next_struct_index = defaultdict(lambda: 1)
    other_objects = []
    stemma_i2c = None
    for singleton, names in objects.items():
        if singleton in STANDARD_BUSES:
            continue
        kind = guess_bus_kind(singleton)
        pins = None
        note = None
        if kind:
            idx = next_struct_index[kind]
            next_struct_index[kind] += 1
            structs = header_info["bus_structs"][kind]
            if idx < len(structs):
                pins = structs[idx]
                note = f"guessed pins (position {idx}); not confirmed in board.c"
        if singleton == "stemma_i2c":
            # Common enough (STEMMA QT/Qwiic connector) to promote to a
            # main board object alongside I2C/SPI/UART/DISPLAY, rather
            # than burying it in the generic "other objects" list.
            stemma_i2c = {"available": True, "names": names, "pins": pins, "note": note}
            continue
        other_objects.append(
            {"names": names, "singleton": f"board_{singleton}_obj", "pins": pins, "note": note}
        )

    display = None
    if displays:
        primary = displays[0]
        display = {"names": primary["names"], "variant": primary["variant"], "size": None}
        board_c = board_dir / "board.c"
        if board_c.exists():
            display["size"] = parse_display_size(
                board_c.read_text(encoding="utf-8"), primary["variant"]
            )

    return {
        "name": header_info["name"] or board_id,
        "mcu": header_info["mcu"],
        "port": info["port"],
        "aliases": info.get("aliases", []),
        "pins": pin_list,
        "objects": {**std_objects, "display": display, "stemma_i2c": stemma_i2c},
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

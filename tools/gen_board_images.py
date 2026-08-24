#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Augment docs/board_data.json with each board's actual image filename,
read from a circuitpython-org checkout's _board/<id>.md front matter
(the `board_image` field). This can differ from the board id — variant
boards sometimes share a sibling's product photo.
"""

import argparse
import json
import re
import sys
from pathlib import Path

CPYORG_CANDIDATES = [
    "../circuitpython-org",
    "~/projects/adafruit/circuitpython-org",
]


def find_cpyorg_root(explicit_path):
    candidates = [explicit_path] if explicit_path else CPYORG_CANDIDATES
    for candidate in candidates:
        path = Path(candidate).expanduser().resolve()
        if (path / "_board").is_dir():
            return path
    raise SystemExit(
        "Could not find a circuitpython-org checkout (needs a _board/ dir).\n"
        f"Tried: {[str(Path(c).expanduser()) for c in candidates]}\n"
        "Pass --cpyorg PATH, or sparse-clone it:\n"
        "  git clone --filter=blob:none --no-checkout --depth=1 "
        "https://github.com/adafruit/circuitpython-org.git\n"
        "  cd circuitpython-org && git sparse-checkout set _board && git checkout"
    )


def get_board_image(cpyorg_root, board_id):
    md_path = cpyorg_root / "_board" / f"{board_id}.md"
    if not md_path.exists():
        return None
    text = md_path.read_text(encoding="utf-8")
    match = re.search(r'^board_image:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
    return match.group(1).strip() if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpyorg", help="Path to a circuitpython-org checkout")
    parser.add_argument(
        "--data",
        default=str(Path(__file__).resolve().parent.parent / "docs" / "board_data.json"),
        help="board_data.json path to read and update in place",
    )
    args = parser.parse_args()

    cpyorg_root = find_cpyorg_root(args.cpyorg)
    print(f"Using circuitpython-org checkout: {cpyorg_root}", file=sys.stderr)

    data_path = Path(args.data)
    data = json.loads(data_path.read_text(encoding="utf-8"))

    found = 0
    missing = []
    for board_id, board in data["boards"].items():
        image = get_board_image(cpyorg_root, board_id)
        board["image"] = image
        if image:
            found += 1
        else:
            missing.append(board_id)

    data_path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Found images for {found}/{len(data['boards'])} boards", file=sys.stderr)
    if missing:
        print(f"No board_image for: {', '.join(missing[:10])}"
              f"{' ...' if len(missing) > 10 else ''}", file=sys.stderr)
    print(f"Updated {data_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

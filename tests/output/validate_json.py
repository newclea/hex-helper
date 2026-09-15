import json
import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_json.py <artifact-root>")

    root = pathlib.Path(sys.argv[1]).resolve(strict=True)
    documents = sorted(root.rglob("*.json"))
    if len(documents) < 7:
        raise AssertionError(
            f"expected at least 7 JSON documents under {root}, found {len(documents)}"
        )

    for path in documents:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise AssertionError(f"JSON artifact escaped output root: {resolved}")
        with resolved.open("r", encoding="utf-8", errors="strict") as stream:
            json.loads(stream.read())

    print(f"[PASS] Python json.loads parsed {len(documents)} UTF-8 JSON documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

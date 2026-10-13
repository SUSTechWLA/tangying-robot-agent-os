#!/usr/bin/env python3
"""Independent stdlib implementation of agent.data.v1's content checksum.

This verifies a checksum, not peer authentication, freshness, or authority.
Number spellings are retained intentionally, matching Go json.Number. The
protocol is explicitly not RFC 8785 canonical JSON.
"""

import argparse
import hashlib
import json
from pathlib import Path


class Number(str):
    """JSON number token, preserved without conversion to float."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def canonical(value):
    if isinstance(value, Number):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(
            canonical(key) + ":" + canonical(value[key]) for key in sorted(value)
        ) + "}"
    if isinstance(value, list):
        return "[" + ",".join(canonical(item) for item in value) + "]"
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for character, escaped in (("<", r"\u003c"), (">", r"\u003e"), ("&", r"\u0026"),
                               ("\u2028", r"\u2028"), ("\u2029", r"\u2029")):
        encoded = encoded.replace(character, escaped)
    return encoded


def verify(raw):
    envelope = json.loads(raw, parse_int=Number, parse_float=Number,
                          object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              ValueError("invalid JSON number: " + value)))
    claimed = envelope.pop("messageId")
    digest = "sha256:" + hashlib.sha256(canonical(envelope).encode("utf-8")).hexdigest()
    if digest != claimed:
        raise ValueError("message checksum does not match")
    return digest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("envelope", type=Path)
    arguments = parser.parse_args()
    print(verify(arguments.envelope.read_text(encoding="utf-8")))

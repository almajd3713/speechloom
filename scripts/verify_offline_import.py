#!/usr/bin/env python3
"""Import installed modules while failing any attempted network connection."""

from __future__ import annotations

import argparse
import importlib
from importlib.resources import files
import socket


def _blocked(*args, **kwargs):
    del args, kwargs
    raise RuntimeError("Network access attempted during offline import verification")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("modules", nargs="+", help="Installed modules to import")
    args = parser.parse_args()
    socket.create_connection = _blocked
    socket.socket.connect = _blocked
    for module in args.modules:
        importlib.import_module(module)
    if "speechloom" in args.modules:
        package = files("speechloom")
        assert package.joinpath("data/registry.json").is_file()
        assert package.joinpath("py.typed").is_file()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

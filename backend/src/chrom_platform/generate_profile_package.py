from __future__ import annotations

import argparse
from pathlib import Path

from .assembly.profile import assemble_profile
from .generate_openapi import generate_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an independently importable trimmed profile package")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument(
        "--openapi-output-dir",
        type=Path,
        help="also emit the static OpenAPI artifact from the assembled package",
    )
    args = parser.parse_args()
    package = assemble_profile(
        args.manifest.resolve(),
        args.output_dir.resolve(),
        source_root=args.source_root.resolve() if args.source_root else None,
    )
    if args.openapi_output_dir is not None:
        generate_package(package, args.openapi_output_dir.resolve())
    print(package)


if __name__ == "__main__":
    main()

"""Write the OpenAPI document to a file. Run with `python -m app.openapi <path>`.

The frontend generates its API types from this file, and CI fails when it is out of date.
Building the document needs the settings, but no database connection and no running server.
"""

import json
import sys
from pathlib import Path

from app.main import create_app


def render() -> str:
    document = create_app().openapi()
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m app.openapi <output path>")
    Path(sys.argv[1]).write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()

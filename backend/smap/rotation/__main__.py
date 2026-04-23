"""`python -m smap.rotation <subcommand>` entry point (D.10)."""

from __future__ import annotations

import typer
from loguru import logger

from . import rotate_transit as _rotate_transit

app = typer.Typer(
    help="SMAP rotation CLI. Runs under the `smap-rotation` AppRole only.",
    no_args_is_help=True,
)


@app.command("rotate-transit")
def rotate_transit_cmd() -> None:
    """Rotate `smap-provider-secret` and rewrap every DEK.

    Idempotent — safe to re-run after a crash. Progress is checkpointed in
    the `rewrap_progress` table; a killed run resumes from the last
    committed chunk.
    """
    summary = _rotate_transit.run()
    logger.info(
        "rotate-transit complete target=v{} api_keys_rewrapped={} search_keys_rewrapped={}",
        summary.target_version,
        summary.api_keys_rewrapped,
        summary.search_keys_rewrapped,
    )


if __name__ == "__main__":
    app()

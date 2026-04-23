"""Phase-B bootstrap CLI package.

Each subcommand is a standalone module that exposes:

  * `run(settings, *, verbose=True) -> BootstrapReport`

The `__main__` module composes them into Typer commands; `all` runs them in
dependency order.

SoC: subcommands import `shared_kernel.infra.*` + `app.config.settings` only.
They NEVER import `contexts.*` or `app.api.*`.
"""

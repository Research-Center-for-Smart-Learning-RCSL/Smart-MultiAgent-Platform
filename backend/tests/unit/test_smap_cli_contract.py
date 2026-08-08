"""The `smap` CLI layer's contract with typer/click.

Every other test of these tools imports the implementation and calls it with
real Python values, so the CLI layer itself was entirely untested. That gap hid
a live defect: typer 0.12.5 against click 8.2+ mis-converts option defaults --
a bool flag arrives as the *string* "False" when absent (truthy) and as None
when passed (falsy), inverting every flag in the repo, while `--help` crashes
outright. It reached the tree in a destructive command whose dry-run default
therefore deleted (`docs/tasks/2026-07-19-session-dir-room-isolation` FU-6).

click is transitive and unpinned, so this arrived without any change on our
side. These tests exercise the conversion through the real CLI machinery so the
next such regression fails here instead of in production.
"""

from __future__ import annotations

import typer
from typer.testing import CliRunner


def _probe_app() -> tuple[typer.Typer, list[dict]]:
    """A miniature of how every smap command declares its options."""
    app = typer.Typer()
    seen: list[dict] = []

    @app.callback()
    def _main() -> None: ...

    @app.command("go")
    def _go(
        token: str | None = typer.Option(None, "--root-token"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        seen.append({"token": token, "force": force})

    return app, seen


def test_a_bool_flag_is_false_when_absent_and_true_when_passed() -> None:
    """The conversion the incident inverted.

    Asserted on identity, not truthiness: the broken version yielded the string
    "False", which `assert not force` would have happily accepted.
    """
    app, seen = _probe_app()

    assert CliRunner().invoke(app, ["go"]).exit_code == 0
    assert CliRunner().invoke(app, ["go", "--force"]).exit_code == 0

    assert seen[0]["force"] is False
    assert seen[1]["force"] is True


def test_a_none_default_option_stays_none() -> None:
    """`--root-token` and friends default to None across smap.bootstrap. The
    broken version produced the string "None", which is truthy -- so a command
    branching on "did the operator pass a token?" took the wrong branch."""
    app, seen = _probe_app()

    CliRunner().invoke(app, ["go"])
    CliRunner().invoke(app, ["go", "--root-token", "hvs.example"])

    assert seen[0]["token"] is None
    assert seen[1]["token"] == "hvs.example"


def test_help_renders_for_every_smap_cli() -> None:
    """`--help` crashed with `Parameter.make_metavar()` on all three apps.

    It is the only place an operator learns that a destructive command needs
    arming, so a crash here is a safety problem, not a cosmetic one.
    """
    from smap.bootstrap.__main__ import app as bootstrap_app
    from smap.examples.__main__ import app as examples_app
    from smap.maintenance.__main__ import app as maintenance_app
    from smap.rotation.__main__ import app as rotation_app

    for name, app in (
        ("bootstrap", bootstrap_app),
        ("rotation", rotation_app),
        ("maintenance", maintenance_app),
        ("examples", examples_app),
    ):
        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == 0, f"{name} --help failed: {result.output}"
        assert "Usage:" in result.output, name


def test_single_command_apps_still_require_their_subcommand_name() -> None:
    """Typer collapses a one-command app into the root, which makes the
    documented `python -m smap.rotation rotate-transit` fail with "Got
    unexpected extra argument". Both single-command apps carry a callback to
    stay in group mode; this pins that, since the collapse returns silently the
    moment someone removes it."""
    from smap.examples.__main__ import app as examples_app
    from smap.maintenance.__main__ import app as maintenance_app
    from smap.rotation.__main__ import app as rotation_app

    for name, app, command in (
        ("rotation", rotation_app, "rotate-transit"),
        ("maintenance", maintenance_app, "purge-session-dirs"),
        ("examples", examples_app, "creative-thinking-course"),
    ):
        result = CliRunner().invoke(app, ["--help"])
        assert command in result.output, f"{name} does not expose {command} as a subcommand"

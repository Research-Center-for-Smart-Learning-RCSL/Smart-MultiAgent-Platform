"""Operator CLI that seeds worked activity examples into an existing project.

Example content is documentation and tooling, never platform behavior ([R30.28]):
nothing here is registered at startup and no runtime code path depends on a seeded
row existing. Subcommands import context facades (the maintenance/rotation
precedent), never repositories or API routes.

Like every `smap` CLI this trusts its operator: it calls the facade directly and so
bypasses the HTTP route's Project Owner check. `--owner-user-id` supplies the audit
actor; it authorizes nothing.
"""

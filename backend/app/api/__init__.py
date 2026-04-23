"""HTTP routers.

SoC: routers are *thin*. They import `contexts.{X}.interfaces` facades and
translate HTTP ↔ facade calls. No SQL, no domain logic, no direct infra.
Enforced by import-linter `pyproject.toml` contracts.
"""

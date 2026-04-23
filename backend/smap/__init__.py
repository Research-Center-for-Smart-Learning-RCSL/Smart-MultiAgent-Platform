"""`smap` — operator-only tooling (bootstrap CLI, rotation commands).

Nothing in this namespace is served over HTTP. It is imported by the host
process (operator workstation or a compose `run --rm` invocation) to perform
one-shot administrative tasks; see `docs/operations.md` §5 and
`docs/implement/B-infrastructure.md` §B.4.
"""

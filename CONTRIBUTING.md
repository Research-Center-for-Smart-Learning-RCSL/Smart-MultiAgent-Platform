# Contributing to SMAP

Thanks for considering a contribution to **SMAP — Smart Multi-Agent Platform**. This document covers the legal and procedural requirements; coding standards are documented in the per-context conventions inside `backend/contexts/*/` and `frontend/src/slices/*/`.

---

## License of contributions

SMAP is distributed under the **GNU Affero General Public License v3.0 or later** (see [LICENSE](./LICENSE)). The Project Owner additionally offers commercial licenses for organizations that cannot accept AGPL terms.

To make dual-licensing possible, **every contributor must sign a Contributor License Agreement (CLA)** before their pull request can be merged.

- **Individuals**: sign the [Individual CLA](./CLA/CLA-INDIVIDUAL.md).
- **Organizations** (employees contributing on behalf of an employer): sign the [Corporate CLA](./CLA/CLA-ENTITY.md).

Until the CLA Assistant GitHub App is installed, sign by adding this line to the pull-request description:

```
I have read and agree to the SMAP Individual CLA v1.0.
Signed-off-by: <Your Full Name> <your-email@example.com>
```

A maintainer will not merge a PR that lacks this attestation.

### DCO sign-off on each commit

In addition to the CLA, every commit must carry a [Developer Certificate of Origin](https://developercertificate.org/) sign-off line. Append `-s` to your commit command:

```bash
git commit -s -m "feat(keys): add per-key quota threshold worker"
```

This adds a `Signed-off-by:` trailer that asserts you have the right to submit the change under the open-source license.

---

## Before opening a pull request

1. **Cite a requirement ID.** Commits, PR titles, and test docstrings reference at least one `[Rxx.yy]` ID from `REQUIREMENTS.md`. If your change requires a new requirement, add it to `REQUIREMENTS.md` in the same PR.
2. **Pass local gates.** Run `make lint`, `make typecheck`, and `make test` locally. CI re-runs these and will block merge on failure.
3. **Stay inside the bounded-context boundaries.** `import-linter` enforces the four-layer rule (`domain` → `infrastructure` → `application` → `interfaces`). Routers may import only from `interfaces`.
4. **Keep specifications English.** All files under `docs/`, `deploy/`, and the root SRS are English-only. PR descriptions and review comments may use zh-TW.
5. **No secrets in Git.** `.env` files, `*.pem`, `*.key`, `*.crt`, and anything under `secrets/` are git-ignored. If you discover a secret in history, see [SECURITY.md](./SECURITY.md).

## Reporting bugs vs. security issues

- **Functional bugs**: open a GitHub Issue with reproduction steps.
- **Security vulnerabilities**: **do not** open a public issue. Follow the disclosure process in [SECURITY.md](./SECURITY.md).

## Commercial licensing and bulk contributions

For dual-license inquiries, large architectural contributions, or anything that does not fit the standard PR flow, open a **GitHub Discussion** under the **"Commercial / Licensing"** category instead of an issue.

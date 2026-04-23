"""Transit master rotation operator CLI (D.10).

Lives under `smap.rotation` (not `smap.bootstrap`) so the operator-only
`smap-rotation` AppRole can be scoped to exactly the commands this package
exposes. The backend AppRole must NOT be able to run these.

Subcommands:
  * ``rotate-transit`` — rotate `smap-provider-secret` and rewrap every DEK.

`deploy/vault/policies/smap-rotation.hcl` is the policy; this CLI is the
only thing that exercises it in normal operation.
"""

from __future__ import annotations

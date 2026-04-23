"""Provider live-validation probes (D.3, R7.02).

One adapter per provider. Each probe is a pure async callable:

    async def probe(secret: str) -> ProbeResult

Adapters never touch the DB, never raise, never log the secret. They return
a ``ProbeResult`` that the `UploadKeyService` persists onto the row.

Dispatch: `PROBES[provider]` returns the callable. Tests override with
``respx``; production uses the default `httpx.AsyncClient`.
"""

from __future__ import annotations

from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.probes.anthropic import probe_anthropic
from contexts.keys.infrastructure.probes.base import ProbeResult, ProbeStatus
from contexts.keys.infrastructure.probes.cohere import probe_cohere
from contexts.keys.infrastructure.probes.gemini import probe_gemini
from contexts.keys.infrastructure.probes.openai import probe_openai
from contexts.keys.infrastructure.probes.voyage import probe_voyage

# Static dispatch table — callable per provider.
PROBES = {
    ApiKeyProvider.CLAUDE: probe_anthropic,
    ApiKeyProvider.OPENAI: probe_openai,
    ApiKeyProvider.GEMINI: probe_gemini,
    ApiKeyProvider.VOYAGE: probe_voyage,
    ApiKeyProvider.COHERE: probe_cohere,
}


async def probe(provider: ApiKeyProvider, secret: str) -> ProbeResult:
    """Run the provider's hard-coded probe (R7.02)."""
    return await PROBES[provider](secret)


__all__ = ["PROBES", "ProbeResult", "ProbeStatus", "probe"]

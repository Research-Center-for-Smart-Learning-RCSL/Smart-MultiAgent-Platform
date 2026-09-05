"""Provider live-validation probes (D.3, R7.02).

One adapter per provider. Each probe is a pure async callable:

    async def probe(secret: str) -> ProbeResult

Adapters never touch the DB, never raise, never log the secret. They return
a ``ProbeResult`` that the `UploadKeyService` persists onto the row.

Dispatch: `PROBES[provider]` returns the callable. Tests override with
``respx``; production uses the default `httpx.AsyncClient`.
"""

from __future__ import annotations

from typing import Any

from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.probes.anthropic import probe_anthropic
from contexts.keys.infrastructure.probes.base import ProbeResult, ProbeStatus
from contexts.keys.infrastructure.probes.cohere import probe_cohere
from contexts.keys.infrastructure.probes.gemini import probe_gemini
from contexts.keys.infrastructure.probes.openai import probe_openai
from contexts.keys.infrastructure.probes.openai_compat import probe_openai_compat
from contexts.keys.infrastructure.probes.voyage import probe_voyage

# Static dispatch table — callable per provider (excluding openai_compat,
# which takes an additional base_url kwarg and is dispatched separately).
PROBES = {
    ApiKeyProvider.CLAUDE: probe_anthropic,
    ApiKeyProvider.OPENAI: probe_openai,
    ApiKeyProvider.GEMINI: probe_gemini,
    ApiKeyProvider.VOYAGE: probe_voyage,
    ApiKeyProvider.COHERE: probe_cohere,
}


async def probe(provider: ApiKeyProvider, secret: str, config: dict[str, Any] | None = None) -> ProbeResult:
    """Run the provider's probe (R7.02).

    For ``OPENAI_COMPAT``, extracts ``base_url`` from ``config`` and passes
    it to the probe. For all other providers, ``config`` is ignored.
    """
    if provider is ApiKeyProvider.OPENAI_COMPAT:
        if not config or not config.get("base_url"):
            return ProbeResult.failed("config.base_url is required for openai_compat")
        return await probe_openai_compat(secret, base_url=config["base_url"])
    return await PROBES[provider](secret)


__all__ = ["PROBES", "ProbeResult", "ProbeStatus", "probe"]

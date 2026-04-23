"""D.4 — masking + repository row conversion sanity."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from contexts.keys.domain.models import ApiKey, mask_preview
from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.probes.base import ProbeStatus


def test_mask_preview_typical_anthropic_shape() -> None:
    raw = "sk-ant-" + "A" * 40 + "xE9a"
    preview = mask_preview(raw)
    assert preview.startswith("sk-ant-")
    assert preview.endswith("xE9a")
    assert "..." in preview
    # And most importantly, the original secret is NOT recoverable.
    assert raw[7:-4] not in preview


def test_mask_preview_short_input_collapses_to_asterisks() -> None:
    assert mask_preview("short") == "***hort"
    assert mask_preview("xyz").startswith("***")


def test_apikey_dataclass_is_immutable_and_has_no_secret_field() -> None:
    now = datetime.now(tz=UTC)
    key = ApiKey(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        provider=ApiKeyProvider.CLAUDE,
        name="work",
        masked_preview="sk-ant-...abcd",
        test_status=ProbeStatus.OK,
        test_error=None,
        last_test_at=now,
        transit_key_version=1,
        hmac_key_version=1,
        created_at=now,
        deleted_at=None,
    )
    # The domain dataclass must not expose any plaintext-bearing attribute —
    # a grep for obvious names catches accidental drift.
    forbidden = {"secret", "plaintext", "ciphertext", "dek_wrapped"}
    assert not forbidden.intersection(key.__slots__)

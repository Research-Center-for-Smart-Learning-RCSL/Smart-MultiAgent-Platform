"""HTTP-based multi-provider LLM chat completer for triple extraction (R11.03)."""

from __future__ import annotations

import httpx

__all__ = ["HttpChatCompleter"]

_TIMEOUT = 90.0


class HttpChatCompleter:
    """Concrete ChatCompleter — provider detected from API-key prefix."""

    async def complete(self, *, prompt: str, api_key: str) -> str:
        if api_key.startswith("sk-ant-"):
            return await self._claude(prompt, api_key)
        if api_key.startswith(("sk-", "sk-proj-")):
            return await self._openai(prompt, api_key)
        return await self._gemini(prompt, api_key)

    async def _claude(self, prompt: str, api_key: str) -> str:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]  # type: ignore[no-any-return]

    async def _openai(self, prompt: str, api_key: str) -> str:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]  # type: ignore[no-any-return]

    async def _gemini(self, prompt: str, api_key: str) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={api_key}"
        )
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            resp.raise_for_status()
            cands = resp.json().get("candidates") or []
            if not cands:
                return ""
            return cands[0]["content"]["parts"][0]["text"]  # type: ignore[no-any-return]

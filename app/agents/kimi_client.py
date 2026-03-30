import os
import asyncio
import json
from typing import Any, AsyncGenerator

import httpx


class KimiClient:
    def __init__(self) -> None:
        self.api_key = (
            os.getenv("KIMI_API_KEY", "").strip()
            or os.getenv("MOONSHOT_API_KEY", "").strip()
        )
        self.base_url = os.getenv(
            "KIMI_BASE_URL",
            "https://api.moonshot.cn/v1",
        ).rstrip("/")
        self.model = os.getenv("KIMI_MODEL", "kimi-k2.5")
        self.connect_timeout = float(os.getenv("KIMI_CONNECT_TIMEOUT", "15"))
        self.read_timeout = float(os.getenv("KIMI_READ_TIMEOUT", "180"))
        self.write_timeout = float(os.getenv("KIMI_WRITE_TIMEOUT", "30"))
        self.pool_timeout = float(os.getenv("KIMI_POOL_TIMEOUT", "30"))
        self.max_retries = int(os.getenv("KIMI_MAX_RETRIES", "2"))
        self.temperature = float(os.getenv("KIMI_TEMPERATURE", "0.7"))

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        force_json_object: bool = False,
        stream: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if force_json_object:
            payload["response_format"] = {"type": "json_object"}
        if stream:
            payload["stream"] = True
        if self.model.lower() != "kimi-k2.5":
            payload["temperature"] = self.temperature
        return payload

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )

    @staticmethod
    def _extract_content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type in {"text", "output_text"}:
                    text_parts.append(item.get("text", ""))
            return "".join(text_parts)
        return str(content)

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        force_json_object: bool = False,
    ) -> str:
        if not self.available:
            raise RuntimeError("KIMI_API_KEY/MOONSHOT_API_KEY is not configured.")
        payload = self._build_payload(
            system_prompt,
            user_prompt,
            force_json_object=force_json_object,
            stream=False,
        )

        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = self._timeout()
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    if resp.is_error:
                        detail = resp.text
                        try:
                            detail = resp.json()
                        except Exception:
                            pass
                        raise RuntimeError(f"Kimi API error {resp.status_code}: {detail}")
                    data = resp.json()
                break
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"Kimi timeout after {attempt + 1} attempts: {repr(exc)}"
                    ) from exc
                await asyncio.sleep(0.8 * (2 ** attempt))
        if last_exc is not None and "data" not in locals():
            raise RuntimeError(f"Kimi timeout: {repr(last_exc)}")
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise RuntimeError(f"Unexpected Kimi response schema: {data}") from exc
        merged = self._extract_content_text(content).strip()
        if merged:
            return merged
        raise RuntimeError(f"Kimi returned empty content blocks: {content}")

    async def complete_stream(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> AsyncGenerator[str, None]:
        if not self.available:
            raise RuntimeError("KIMI_API_KEY/MOONSHOT_API_KEY is not configured.")

        payload = self._build_payload(system_prompt, user_prompt, stream=True)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        timeout = self._timeout()
        emitted_any = False

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    ) as resp:
                        if resp.is_error:
                            detail = await resp.aread()
                            text = detail.decode("utf-8", errors="ignore")
                            try:
                                error_json = json.loads(text)
                                raise RuntimeError(f"Kimi API error {resp.status_code}: {error_json}")
                            except json.JSONDecodeError:
                                raise RuntimeError(f"Kimi API error {resp.status_code}: {text}")

                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            if not raw:
                                continue
                            if raw == "[DONE]":
                                return

                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            if "error" in event:
                                raise RuntimeError(f"Kimi API stream error: {event['error']}")

                            choices = event.get("choices")
                            if not isinstance(choices, list) or not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            if not isinstance(delta, dict):
                                continue

                            delta_text = self._extract_content_text(delta.get("content"))
                            if delta_text:
                                emitted_any = True
                                yield delta_text
                        return
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                if emitted_any:
                    raise RuntimeError(f"Kimi stream timeout after partial output: {repr(exc)}") from exc
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"Kimi stream timeout after {attempt + 1} attempts: {repr(exc)}"
                    ) from exc
                await asyncio.sleep(0.8 * (2 ** attempt))

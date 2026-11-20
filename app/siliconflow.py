from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass
from pathlib import Path

import requests


API_BASE = "https://api.siliconflow.cn/v1"
FREE_IMAGE_MODEL = "Kwai-Kolors/Kolors"
IMAGE_MODELS = [
    FREE_IMAGE_MODEL,
    "Tongyi-MAI/Z-Image-Turbo",
    "OpenMOSS-Team/MOSS-Image",
]


class SiliconFlowError(RuntimeError):
    pass


@dataclass
class SiliconFlowClient:
    api_key: str
    chat_model: str
    timeout: int = 240

    def __post_init__(self) -> None:
        self.api_key = (self.api_key or "").strip()
        self.chat_model = (self.chat_model or "").strip()
        if not self.api_key:
            raise SiliconFlowError("请填写硅基流动 API Key")
        if not self.chat_model:
            raise SiliconFlowError("请填写文本模型名称")
        self.session = requests.Session()
        # The desktop app talks directly to SiliconFlow. This also avoids stale
        # system proxies in the test environment.
        self.session.trust_env = False

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _request(
        self, method: str, url: str, timeout: int | None = None, **kwargs
    ) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self.session.request(
                    method, url, headers=self.headers, timeout=timeout or self.timeout, **kwargs
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    time.sleep(2)
                    continue
                if not response.ok:
                    detail = response.text[:800].replace(self.api_key, "***")
                    raise SiliconFlowError(f"硅基流动接口错误 HTTP {response.status_code}: {detail}")
                return response
            except (requests.RequestException, SiliconFlowError) as exc:
                last_error = exc
                if attempt == 0 and not isinstance(exc, SiliconFlowError):
                    time.sleep(2)
                    continue
                raise
        raise SiliconFlowError(str(last_error or "硅基流动请求失败"))

    def list_models(self) -> list[str]:
        response = self._request("GET", f"{API_BASE}/models")
        payload = response.json()
        return [item.get("id", "") for item in payload.get("data", []) if item.get("id")]

    def _chat(self, messages: list[dict], max_tokens: int, temperature: float) -> str:
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = self._request("POST", f"{API_BASE}/chat/completions", json=payload)
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SiliconFlowError(f"模型返回格式异常: {str(data)[:500]}") from exc
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        content = (content or "").strip()
        if not content:
            raise SiliconFlowError("模型返回空内容")
        return content

    def chat(self, system_prompt: str, user_prompt: str, max_tokens: int = 8000) -> str:
        return self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens,
            0.25,
        )

    def chat_vision(
        self,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[Path],
        max_tokens: int = 3000,
    ) -> str:
        from PIL import Image, ImageOps

        parts: list[dict] = [{"type": "text", "text": user_prompt}]
        for index, path in enumerate(image_paths, 1):
            with Image.open(path) as raw:
                image = ImageOps.exif_transpose(raw).convert("RGB")
                image.thumbnail((1200, 1200), Image.LANCZOS)
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=84, optimize=True)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            parts.append({"type": "text", "text": f"图片 {index}"})
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                }
            )
        return self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": parts},
            ],
            max_tokens,
            0.1,
        )

    def generate_image(self, prompt: str, model: str) -> bytes:
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "image_size": "1024x1024",
            "num_inference_steps": 20,
        }
        if model == FREE_IMAGE_MODEL:
            payload["guidance_scale"] = 7.5
        response = self._request(
            "POST",
            f"{API_BASE}/images/generations",
            json=payload,
            timeout=max(self.timeout, 600),
        )
        data = response.json()
        images = data.get("images") or []
        if not images:
            raise SiliconFlowError(f"生图接口未返回图片: {str(data)[:500]}")
        item = images[0] or {}
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        url = item.get("url")
        if not url:
            raise SiliconFlowError("生图接口未返回 url 或 base64 数据")
        image_response = self.session.get(url, timeout=180)
        image_response.raise_for_status()
        return image_response.content


def bytes_to_image(data: bytes):
    from PIL import Image

    return Image.open(io.BytesIO(data))

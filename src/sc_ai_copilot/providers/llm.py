from __future__ import annotations

import httpx


def list_models(provider: str, base_url: str) -> list[str]:
    base_url = base_url.rstrip("/")
    if provider == "ollama":
        response = httpx.get(f"{base_url}/api/tags", timeout=5)
        response.raise_for_status()
        models = [item.get("name") for item in response.json().get("models", [])]
    elif provider == "llama.cpp":
        endpoint = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
        response = httpx.get(endpoint, timeout=5)
        response.raise_for_status()
        models = [item.get("id") for item in response.json().get("data", [])]
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    return sorted({str(model) for model in models if model}, key=str.casefold)


def chat(provider: str, base_url: str, model: str, prompt: str, system_prompt: str = "") -> str:
    if not model.strip():
        return "Kein LLM-Modell konfiguriert. Bitte in den Einstellungen ein Modell eintragen."
    base_url = base_url.rstrip("/")
    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": prompt})
    if provider == "ollama":
        response = httpx.post(
            f"{base_url}/api/chat",
            json={"model": model, "messages": messages, "stream": False},
            timeout=90,
        )
        response.raise_for_status()
        return response.json()["message"]["content"].strip()
    if provider == "llama.cpp":
        endpoint = f"{base_url}/chat/completions" if base_url.endswith("/v1") else f"{base_url}/v1/chat/completions"
        response = httpx.post(
            endpoint,
            json={"model": model, "messages": messages, "stream": False},
            timeout=90,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    raise ValueError(f"Unsupported LLM provider: {provider}")

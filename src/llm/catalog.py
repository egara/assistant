"""LLM Catalog and Model Discovery manager for Assistant."""

import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

from openai import OpenAI, Timeout

logger = logging.getLogger("assistant.llm.catalog")


class ModelCatalog:
    """Manages configured LLM endpoints, model presets, and dynamic discovery."""

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None):
        self.config: Dict[str, Any] = llm_config or {}
        self.catalog: Dict[str, Dict[str, Any]] = {}
        self._load_configured_models()
        self.discover_endpoint_models()
        self.default_model_key: str = self._resolve_default_model_key()

    def _load_configured_models(self) -> None:
        """Parse configuration and instantiate OpenAI clients."""
        models_spec = self.config.get("models")
        if isinstance(models_spec, dict) and models_spec:
            for key, spec in models_spec.items():
                if isinstance(spec, dict):
                    m_key = str(key).strip()
                    m_name = spec.get("name") or spec.get("model") or m_key
                    m_url = spec.get("base_url", "http://127.0.0.1:8080/v1")
                    m_key_auth = spec.get("api_key", "sk-none")
                    m_timeout = float(spec.get("timeout", 180.0))
                    m_vision = spec.get("vision_enabled", True)
                    m_provider = spec.get("provider_name", "local")
                    m_max_rounds = spec.get("max_tool_rounds")

                    client_inst = OpenAI(
                        base_url=m_url,
                        api_key=m_key_auth,
                        timeout=Timeout(m_timeout, connect=10.0),
                    )

                    self.catalog[m_key] = {
                        "key": m_key,
                        "name": m_name,
                        "client": client_inst,
                        "base_url": m_url,
                        "api_key": m_key_auth,
                        "vision_enabled": m_vision,
                        "provider_name": m_provider,
                        "max_tool_rounds": m_max_rounds,
                        "is_preset": True,
                    }

        # Backward compatibility: if "models" is not defined, read legacy primary and fallback
        if not self.catalog:
            primary_name = self.config.get("model", "Hermes-3-8B")
            primary_url = self.config.get("base_url", "http://127.0.0.1:8080/v1")
            primary_key = self.config.get("api_key", "sk-none")
            primary_timeout = float(self.config.get("timeout", 180.0))
            primary_vision = self.config.get("vision_enabled", True)
            primary_provider = self.config.get("provider_name", "local")
            primary_max_rounds = self.config.get("max_tool_rounds")

            primary_client = OpenAI(
                base_url=primary_url,
                api_key=primary_key,
                timeout=Timeout(primary_timeout, connect=10.0),
            )

            primary_id = primary_name.split(":")[0].lower() if ":" in primary_name else "primary"
            self.catalog[primary_id] = {
                "key": primary_id,
                "name": primary_name,
                "client": primary_client,
                "base_url": primary_url,
                "api_key": primary_key,
                "vision_enabled": primary_vision,
                "provider_name": primary_provider,
                "max_tool_rounds": primary_max_rounds,
                "is_preset": True,
            }

            fallback_conf = self.config.get("fallback")
            if isinstance(fallback_conf, dict):
                fb_name = fallback_conf.get("model", "GLM-5.3-Flash")
                fb_url = fallback_conf.get("base_url", "http://127.0.0.1:8010/v1")
                fb_key = fallback_conf.get("api_key", "sk-none")
                fb_timeout = float(fallback_conf.get("timeout", 180.0))
                fb_vision = fallback_conf.get("vision_enabled", True)
                fb_provider = fallback_conf.get("provider_name", "local")

                fb_client = OpenAI(
                    base_url=fb_url,
                    api_key=fb_key,
                    timeout=Timeout(fb_timeout, connect=10.0),
                )
                fb_id = fb_name.split(":")[0].lower() if ":" in fb_name else "fallback"
                self.catalog[fb_id] = {
                    "key": fb_id,
                    "name": fb_name,
                    "client": fb_client,
                    "base_url": fb_url,
                    "api_key": fb_key,
                    "vision_enabled": fb_vision,
                    "provider_name": fb_provider,
                    "is_preset": True,
                }

    def discover_endpoint_models(self) -> None:
        """Query each configured provider base_url/models endpoint to discover models."""
        seen_urls = set()
        endpoints = []
        for info in list(self.catalog.values()):
            b_url = info.get("base_url")
            if not b_url or b_url in seen_urls:
                continue
            seen_urls.add(b_url)
            endpoints.append(info)

        for base_info in endpoints:
            b_url = base_info.get("base_url", "").rstrip("/")
            models_url = f"{b_url}/models"
            p_name = base_info.get("provider_name", "Local")
            auth_header = f"Bearer {base_info.get('api_key', 'sk-none')}"
            req = urllib.request.Request(models_url, headers={"Authorization": auth_header})
            try:
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for m in data.get("data", []):
                        m_id = m.get("id")
                        if not m_id:
                            continue
                        already_in_catalog = any(
                            info.get("base_url", "").rstrip("/") == b_url and (info.get("name") == m_id or info.get("key") == m_id)
                            for info in self.catalog.values()
                        )
                        if not already_in_catalog:
                            self.catalog[m_id] = {
                                "key": m_id,
                                "name": m_id,
                                "client": base_info.get("client"),
                                "base_url": base_info.get("base_url"),
                                "api_key": base_info.get("api_key"),
                                "vision_enabled": base_info.get("vision_enabled", True),
                                "provider_name": p_name,
                                "is_preset": False,
                            }
                            logger.info(f"[Discovery] Found model '{m_id}' on {p_name} ({b_url})")
            except Exception as e:
                logger.debug(f"[Discovery] Could not fetch models from {models_url}: {e}")

    def _resolve_default_model_key(self) -> str:
        """Determine default initial active model."""
        def_key = self.config.get("default")
        if def_key and def_key in self.catalog:
            return def_key
        return next(iter(self.catalog.keys())) if self.catalog else ""

    def get_model(self, key: str) -> Optional[Dict[str, Any]]:
        """Get model specification by key, falling back to first available."""
        if key in self.catalog:
            return self.catalog[key]
        if self.catalog:
            return next(iter(self.catalog.values()))
        return None

    def is_vision_supported(self, key: str) -> bool:
        """Check whether the model corresponding to key supports multimodal vision inputs."""
        model_info = self.get_model(key)
        if model_info:
            return bool(model_info.get("vision_enabled", False))
        return False

    def get_structured_models_list(self, active_key: str) -> List[Dict[str, Any]]:
        """Return structured list of items (headers and model entries) for the QML popup.

        Ordered by:
          1. Config Presets (Favorites / Defaults)
          2. Discovered Models grouped by provider / endpoint
        """
        res = []

        # 1. Configured Presets Header & Items
        presets = [info for info in self.catalog.values() if info.get("is_preset")]
        if presets:
            res.append({
                "is_header": True,
                "header_title": "󰚩 MODELOS CONFIGURADOS",
                "key": "",
                "name": "",
                "url": "",
                "active": False,
            })
            for info in presets:
                k = info.get("key")
                res.append({
                    "is_header": False,
                    "header_title": "",
                    "key": k,
                    "name": info.get("name", k),
                    "url": info.get("base_url", ""),
                    "active": (k == active_key),
                })

        # 2. Group Discovered Models by Provider
        discovered_by_provider: Dict[str, List[Dict[str, Any]]] = {}
        for info in self.catalog.values():
            if not info.get("is_preset"):
                p_name = info.get("provider_name", "Endpoint Local")
                b_url = info.get("base_url", "")
                group_key = f"{p_name} ({b_url})"
                discovered_by_provider.setdefault(group_key, []).append(info)

        for group_header, items in discovered_by_provider.items():
            res.append({
                "is_header": True,
                "header_title": f"󰒋 {group_header.upper()}",
                "key": "",
                "name": "",
                "url": "",
                "active": False,
            })
            for info in items:
                k = info.get("key")
                res.append({
                    "is_header": False,
                    "header_title": "",
                    "key": k,
                    "name": info.get("name", k),
                    "url": info.get("base_url", ""),
                    "active": (k == active_key),
                })

        return res

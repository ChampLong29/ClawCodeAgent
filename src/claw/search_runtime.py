"""Search runtime for CodeAgent."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class SearchProvider:
    """A search provider configuration."""
    name: str
    provider: str  # searxng, brave, tavily
    base_url: str
    api_key_env: Optional[str] = None
    default_max_results: int = 5

    # Support both camelCase and snake_case
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SearchProvider:
        # Handle camelCase vs snake_case
        base_url = data.get("baseUrl") or data.get("base_url", "")
        api_key_env = data.get("apiKeyEnv") or data.get("api_key_env")

        return cls(
            name=data.get("name", ""),
            provider=data.get("provider", ""),
            base_url=base_url,
            api_key_env=api_key_env,
            default_max_results=data.get("default_max_results", data.get("defaultMaxResults", 5)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "default_max_results": self.default_max_results,
        }


class SearchRuntime(RuntimeBase):
    """Search provider runtime.

    Supports env var injection:
    - SEARXNG_BASE_URL
    - BRAVE_SEARCH_API_KEY
    - TAVILY_API_KEY

    Discovery paths:
    - .claw-search.json
    - .claude/search.json
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.providers = self._discover()
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides."""
        import os

        # Apply env vars to existing providers or create new ones
        if os.environ.get("SEARXNG_BASE_URL"):
            searxng_found = False
            for p in self.providers:
                if p.provider == "searxng":
                    p.base_url = os.environ["SEARXNG_BASE_URL"]
                    searxng_found = True
                    break
            if not searxng_found:
                self.providers.append(SearchProvider(
                    name="searxng-from-env",
                    provider="searxng",
                    base_url=os.environ["SEARXNG_BASE_URL"],
                ))

        if os.environ.get("BRAVE_SEARCH_API_KEY"):
            brave_found = False
            for p in self.providers:
                if p.provider == "brave":
                    p.api_key_env = "BRAVE_SEARCH_API_KEY"
                    brave_found = True
                    break
            if not brave_found:
                self.providers.append(SearchProvider(
                    name="brave-from-env",
                    provider="brave",
                    base_url="https://api.search.brave.com/res/v1/web/search",
                    api_key_env="BRAVE_SEARCH_API_KEY",
                ))

        if os.environ.get("TAVILY_API_KEY"):
            tavily_found = False
            for p in self.providers:
                if p.provider == "tavily":
                    tavily_found = True
                    break
            if not tavily_found:
                self.providers.append(SearchProvider(
                    name="tavily-from-env",
                    provider="tavily",
                    base_url="https://api.tavily.com",
                    api_key_env="TAVILY_API_KEY",
                ))

    def _discover(self) -> List[SearchProvider]:
        """Discover search configuration."""
        search_paths = [
            os.path.join(self.cwd, ".claw-search.json"),
            os.path.join(self.cwd, ".claude", "search.json"),
        ]

        for filepath in search_paths:
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_providers(data)
                except (json.JSONDecodeError, OSError):
                    continue

        return []

    def _parse_providers(self, data: Dict[str, Any]) -> List[SearchProvider]:
        """Parse providers from configuration."""
        providers = []
        for p_data in data.get("providers", []):
            providers.append(SearchProvider.from_dict(p_data))
        return providers

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "providers": [p.to_dict() for p in self.providers],
            "count": len(self.providers),
        }

    def list_providers(self) -> List[Dict[str, Any]]:
        """List all providers."""
        return [p.to_dict() for p in self.providers]

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.providers:
            return "No search providers configured."

        names = [p.name for p in self.providers]
        return f"[Search Providers] {', '.join(names)}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.providers:
            return ""

        provider_types = [p.provider for p in self.providers]
        return f"Available search providers: {', '.join(provider_types)}"

    def search(self, query: str, provider_name: Optional[str] = None) -> Dict[str, Any]:
        """Perform a search using the specified or first available provider."""
        if not self.providers:
            return {"query": query, "results": [], "error": "No search providers configured"}

        # Select provider
        provider = None
        if provider_name:
            for p in self.providers:
                if p.name == provider_name:
                    provider = p
                    break
        if not provider:
            provider = self.providers[0]

        # Dispatch to provider-specific implementation
        try:
            if provider.provider == "searxng":
                return self._search_searxng(query, provider)
            elif provider.provider == "brave":
                return self._search_brave(query, provider)
            elif provider.provider == "tavily":
                return self._search_tavily(query, provider)
            else:
                return {"query": query, "provider": provider.name, "results": [], "error": f"Unknown provider type: {provider.provider}"}
        except Exception as e:
            return {"query": query, "provider": provider.name, "results": [], "error": str(e)}

    def _search_searxng(self, query: str, provider: SearchProvider) -> Dict[str, Any]:
        """Search using SearXNG instance."""
        base_url = provider.base_url.rstrip("/")
        params = urllib.parse.urlencode({"q": query, "format": "json"})
        url = f"{base_url}/search?{params}"

        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results = []
                for r in data.get("results", [])[:provider.default_max_results]:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("content", "") or r.get("snippet", ""),
                    })
                return {"query": query, "provider": provider.name, "results": results}
        except urllib.error.HTTPError as e:
            return {"query": query, "provider": provider.name, "results": [], "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"query": query, "provider": provider.name, "results": [], "error": str(e)}

    def _search_brave(self, query: str, provider: SearchProvider) -> Dict[str, Any]:
        """Search using Brave Search API."""
        api_key = os.environ.get(provider.api_key_env or "BRAVE_SEARCH_API_KEY", "")
        if not api_key:
            return {"query": query, "provider": provider.name, "results": [], "error": "No API key configured"}

        base_url = provider.base_url.rstrip("/")
        params = urllib.parse.urlencode({"q": query, "count": str(provider.default_max_results)})
        url = f"{base_url}?{params}"

        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("X-Subscription-Token", api_key)
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results = []
                for r in data.get("web", {}).get("results", [])[:provider.default_max_results]:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("description", ""),
                    })
                return {"query": query, "provider": provider.name, "results": results}
        except urllib.error.HTTPError as e:
            return {"query": query, "provider": provider.name, "results": [], "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"query": query, "provider": provider.name, "results": [], "error": str(e)}

    def _search_tavily(self, query: str, provider: SearchProvider) -> Dict[str, Any]:
        """Search using Tavily Search API."""
        api_key = os.environ.get(provider.api_key_env or "TAVILY_API_KEY", "")
        if not api_key:
            return {"query": query, "provider": provider.name, "results": [], "error": "No API key configured"}

        base_url = provider.base_url.rstrip("/")
        url = f"{base_url}/search"

        payload = json.dumps({
            "api_key": api_key,
            "query": query,
            "max_results": provider.default_max_results,
        }).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                results = []
                for r in data.get("results", [])[:provider.default_max_results]:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("content", ""),
                    })
                return {"query": query, "provider": provider.name, "results": results}
        except urllib.error.HTTPError as e:
            return {"query": query, "provider": provider.name, "results": [], "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"query": query, "provider": provider.name, "results": [], "error": str(e)}


# Helper function for field name compatibility
def _provider_from_json(data: Dict[str, Any]) -> SearchProvider:
    """Create a SearchProvider from JSON dict, supporting both field name styles."""
    return SearchProvider.from_dict(data)
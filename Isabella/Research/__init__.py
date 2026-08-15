"""Public web research API."""

from .fetch import FetchError, UnsafeURLError, WebFetcher, validate_public_url
from .manager import ResearchManager, load_research_config
from .models import ResearchResult, SearchHit, Source
from .search import DuckDuckGoHTMLProvider, SearchProvider, SearchUnavailableError, build_search_provider
from .sources import build_untrusted_context, sanitize_untrusted_text

__all__ = [
    "DuckDuckGoHTMLProvider", "FetchError", "ResearchManager", "ResearchResult",
    "SearchHit", "SearchProvider", "SearchUnavailableError", "Source", "UnsafeURLError",
    "WebFetcher", "build_search_provider", "build_untrusted_context", "load_research_config",
    "sanitize_untrusted_text", "validate_public_url",
]


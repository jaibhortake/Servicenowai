"""
Catalog-Derived Lexical Indexing System

Builds and maintains an inverted index from ServiceNow catalog items without embeddings.
Enables deterministic, fast catalog retrieval via lexical matching.

Core components:
1. INVERTED INDEX: token/phrase → catalog node IDs
2. QUERY NORMALIZATION: user input → normalized tokens
3. FUZZY MATCHING: edit distance and partial token matching
4. RANKING: exact phrase > token overlap > category relevance
"""

import logging
import re
from typing import Dict, List, Set, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import defaultdict
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# Stopwords to exclude from indexing (language-agnostic common terms)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "he",
    "in", "is", "it", "its", "of", "on", "or", "that", "the", "to", "was", "will",
    "with", "request", "service", "catalog", "item", "access", "provide", "get",
}

# Common synonyms and aliases for expansion (catalog-safe)
ALIAS_EXPANSION = {
    "mobile": {"phone", "smartphone", "device", "cellphone"},
    "phone": {"mobile", "smartphone", "device"},
    "laptop": {"computer", "notebook", "macbook", "pc"},
    "desktop": {"computer", "workstation", "pc"},
    "vpn": {"remote", "access", "connectivity"},
    "software": {"license", "application", "app", "tool"},
    "hardware": {"device", "equipment", "machine"},
    "printer": {"printing", "copier", "scanner"},
    "monitor": {"display", "screen"},
    "keyboard": {"input", "typing"},
    "mouse": {"input", "pointer"},
}


@dataclass
class CatalogIndexEntry:
    """Single catalog item in the index."""
    sys_id: str
    name: str
    short_description: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None

    # Derived fields
    name_tokens: Set[str] = field(default_factory=set)
    desc_tokens: Set[str] = field(default_factory=set)
    category_tokens: Set[str] = field(default_factory=set)
    all_tokens: Set[str] = field(default_factory=set)  # Union of all
    name_phrases: List[str] = field(default_factory=list)  # 2-word phrases from name
    desc_phrases: List[str] = field(default_factory=list)  # Key phrases from description


class CatalogLexicalIndex:
    """
    Inverted index for catalog items.

    Maps: token/phrase → set of catalog sys_ids
    """

    def __init__(self):
        # Inverted index: token → set(sys_id)
        self.token_index: Dict[str, Set[str]] = defaultdict(set)
        # Phrase index: exact phrase → set(sys_id)
        self.phrase_index: Dict[str, Set[str]] = defaultdict(set)
        # Catalog entries for ranking
        self.entries: Dict[str, CatalogIndexEntry] = {}
        # Category → set(sys_id) for category-based retrieval
        self.category_index: Dict[str, Set[str]] = defaultdict(set)

    def add_item(self, item: CatalogIndexEntry):
        """Index a single catalog item."""
        sys_id = item.sys_id
        self.entries[sys_id] = item

        # Tokenize all fields
        item.name_tokens = _tokenize(item.name or "")
        item.desc_tokens = _tokenize(item.short_description or "") | _tokenize(item.description or "")
        item.category_tokens = _tokenize(item.category or "")
        item.all_tokens = item.name_tokens | item.desc_tokens | item.category_tokens

        # Extract 2-word phrases from name (high-value matches)
        item.name_phrases = _extract_phrases(item.name or "", length=2)
        item.desc_phrases = _extract_phrases(item.short_description or "", length=2)

        # Build token inverted index
        for token in item.all_tokens:
            self.token_index[token].add(sys_id)

        # Build phrase inverted index
        for phrase in item.name_phrases + item.desc_phrases:
            self.phrase_index[phrase].add(sys_id)

        # Index by category
        if item.category_tokens:
            for cat_token in item.category_tokens:
                self.category_index[cat_token].add(sys_id)

    def bulk_add(self, items: List[CatalogIndexEntry]):
        """Index multiple items efficiently."""
        for item in items:
            self.add_item(item)
        logger.info(f"Indexed {len(items)} catalog items. "
                   f"Token index size: {len(self.token_index)}, "
                   f"Phrase index size: {len(self.phrase_index)}")

    def search(self, query: str, extra_tokens: Optional[Set[str]] = None) -> List[Tuple[str, float]]:
        """
        Search the index and return ranked results.

        Args:
            query: User query string
            extra_tokens: Additional tokens to include in search (e.g., from LLM expansion)

        Returns: List of (sys_id, score) tuples, sorted by score descending.
        """
        if not query or not query.strip():
            return []

        # Normalize query
        normalized = _normalize_query(query)
        query_tokens = _tokenize(normalized)

        # Merge in extra tokens (e.g., from query expansion)
        if extra_tokens:
            query_tokens = query_tokens | extra_tokens

        query_phrases = _extract_phrases(normalized, length=2)

        logger.debug(f"Search query: '{query}' → tokens={query_tokens}, phrases={query_phrases}, extra={extra_tokens}")

        # Find candidate items (items matching at least one token)
        candidates: Set[str] = set()

        # Exact phrase matches are high-value candidates
        for phrase in query_phrases:
            if phrase in self.phrase_index:
                candidates.update(self.phrase_index[phrase])

        # Token matches
        for token in query_tokens:
            if token in self.token_index:
                candidates.update(self.token_index[token])
            # Fuzzy match: tokens similar to query tokens
            fuzzy_matches = _fuzzy_token_match(token, list(self.token_index.keys()), threshold=0.75)
            for fuzzy_token in fuzzy_matches:
                candidates.update(self.token_index[fuzzy_token])

        logger.debug(f"Found {len(candidates)} candidate items")

        # Score candidates
        scored: List[Tuple[str, float]] = []
        for sys_id in candidates:
            entry = self.entries[sys_id]
            score = _score_match(query_tokens, query_phrases, entry)
            if score > 0:
                scored.append((sys_id, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        logger.debug(f"Top 5 scores: {scored[:5]}")
        return scored

    def clear(self):
        """Clear all indices."""
        self.token_index.clear()
        self.phrase_index.clear()
        self.entries.clear()
        self.category_index.clear()


def _normalize_query(query: str) -> str:
    """Normalize query: lowercase, remove punctuation, normalize whitespace."""
    normalized = query.lower()
    # Remove common punctuation
    normalized = re.sub(r'[^\w\s\-]', ' ', normalized)
    # Normalize whitespace
    normalized = ' '.join(normalized.split())
    return normalized


def _tokenize(text: str) -> Set[str]:
    """
    Tokenize text: normalize, split, remove stopwords and short tokens.

    Returns set of tokens with length >= 3 (except recognized short terms).
    """
    if not text:
        return set()

    normalized = _normalize_query(text)
    tokens = set()

    for token in normalized.split():
        # Skip stopwords
        if token in STOPWORDS:
            continue
        # Skip very short tokens (but allow 2-letter combos we care about)
        if len(token) < 3 and token not in {"ai", "it", "ip", "vm", "os"}:
            continue
        tokens.add(token)

    return tokens


def _extract_phrases(text: str, length: int = 2) -> List[str]:
    """Extract n-word phrases from text (e.g., 2-word phrases for high-value matches)."""
    if not text:
        return []

    normalized = _normalize_query(text)
    tokens = normalized.split()
    phrases = []

    for i in range(len(tokens) - length + 1):
        phrase_tokens = tokens[i:i+length]
        # Skip if phrase contains only stopwords
        if any(t not in STOPWORDS for t in phrase_tokens):
            phrase = ' '.join(phrase_tokens)
            if len(phrase) >= 5:  # Minimum phrase length
                phrases.append(phrase)

    return phrases


def _fuzzy_token_match(query_token: str, candidates: List[str], threshold: float = 0.75) -> List[str]:
    """
    Fuzzy match a query token against candidate tokens using edit distance.

    Returns list of candidate tokens with similarity >= threshold.
    """
    matches = []
    for candidate in candidates:
        # Use difflib's SequenceMatcher for similarity ratio
        ratio = SequenceMatcher(None, query_token, candidate).ratio()
        if ratio >= threshold:
            matches.append(candidate)
    return matches


def _score_match(query_tokens: Set[str], query_phrases: List[str], entry: CatalogIndexEntry) -> float:
    """
    Score how well an entry matches the query.

    Ranking factors (descending importance):
    1. Exact phrase match in name (0.90+ bonus)
    2. Token overlap in name (0.50-0.70) - TITLE IS CRITICAL
    3. Category match (0.15 bonus) - CATEGORY WEIGHTING INCREASED
    4. Token overlap in description (0.05-0.15) - REDUCED WEIGHT
    5. Fuzzy matches (lower weight)

    Returns score in [0.0, 1.0].
    """
    score = 0.0

    # 1. Exact phrase matches in name (highest value)
    phrase_matches_in_name = 0
    for phrase in query_phrases:
        if phrase in entry.name_phrases:
            phrase_matches_in_name += 1
    if phrase_matches_in_name > 0:
        score += min(0.90, phrase_matches_in_name * 0.30)

    # 2. Token overlap in NAME (name/title is MOST CRITICAL)
    # This is the primary ranking factor
    if entry.name_tokens:
        name_overlap = len(query_tokens & entry.name_tokens)
        name_token_coverage = name_overlap / max(len(query_tokens), 1)
        # Increased weight for name matches (0.50 → 0.70)
        score += name_token_coverage * 0.70

    # 3. Category match bonus (INCREASED from 0.10 to 0.15-0.25)
    # Strong bonus for category alignment
    if entry.category_tokens and query_tokens:
        category_overlap = len(query_tokens & entry.category_tokens)
        if category_overlap > 0:
            # Bonus scales with category overlap
            score += min(0.25, category_overlap * 0.15)

    # 4. Token overlap in DESCRIPTION (reduced weight - supporting only)
    # Description matches should not dominate if title doesn't match
    if entry.desc_tokens and query_tokens:
        desc_overlap = len(query_tokens & entry.desc_tokens)
        # Only count if there's also some name match (avoid spurious desc matches)
        if entry.name_tokens and (query_tokens & entry.name_tokens):
            desc_token_coverage = desc_overlap / max(len(query_tokens), 1)
            # Reduced weight from 0.25 → 0.15
            score += desc_token_coverage * 0.15

    # 5. Fuzzy token match bonus (partial credit, lower weight)
    fuzzy_name_matches = 0
    for query_token in query_tokens:
        fuzzy_in_name = any(
            SequenceMatcher(None, query_token, t).ratio() >= 0.8
            for t in entry.name_tokens
        )
        fuzzy_in_desc = any(
            SequenceMatcher(None, query_token, t).ratio() >= 0.8
            for t in entry.desc_tokens
        )
        if fuzzy_in_name:
            fuzzy_name_matches += 1
            score += 0.03

    # Penalty for title mismatch (if none of the query tokens match the name, reduce score)
    if entry.name_tokens and query_tokens:
        if not (query_tokens & entry.name_tokens):
            # No direct name match - penalize heavily
            score *= 0.5
            # But if desc/category has matches, allow some credit
            if (query_tokens & entry.desc_tokens) or (query_tokens & entry.category_tokens):
                score *= 0.8

    # Clamp to [0, 1]
    return min(score, 1.0)


# Global singleton instance
_index: Optional[CatalogLexicalIndex] = None


def get_index() -> CatalogLexicalIndex:
    """Get or create the global index."""
    global _index
    if _index is None:
        _index = CatalogLexicalIndex()
    return _index


def build_index_from_items(items: List[Dict[str, Any]]) -> CatalogLexicalIndex:
    """
    Build a fresh index from catalog items.

    Args:
        items: List of catalog item dicts with sys_id, name, short_description, etc.

    Returns:
        Populated CatalogLexicalIndex.
    """
    index = CatalogLexicalIndex()
    entries = []

    for item in items:
        entry = CatalogIndexEntry(
            sys_id=item.get("sys_id", ""),
            name=item.get("name", ""),
            short_description=item.get("short_description"),
            description=item.get("description"),
            category=_extract_category_value(item.get("category")),
        )
        if entry.sys_id and entry.name:
            entries.append(entry)

    index.bulk_add(entries)
    return index


def _extract_category_value(field: Any) -> Optional[str]:
    """Extract string value from category field (can be dict or string)."""
    if isinstance(field, dict):
        return field.get("display_value") or field.get("value")
    return str(field) if field else None


def search_index(query: str, extra_tokens: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Search the global index and return results as dicts.

    Args:
        query: User query string
        extra_tokens: Additional tokens to include in search (e.g., from LLM expansion)

    Returns: List of {sys_id, name, score} dicts, sorted by score.
    """
    index = get_index()
    extra_set = set(extra_tokens) if extra_tokens else None
    results = index.search(query, extra_tokens=extra_set)
    return [
        {
            "sys_id": sys_id,
            "name": index.entries[sys_id].name,
            "score": score,
            "short_description": index.entries[sys_id].short_description,
        }
        for sys_id, score in results
    ]


def initialize_index(items: List[Dict[str, Any]]):
    """Initialize the global index with catalog items."""
    global _index
    _index = build_index_from_items(items)
    logger.info(f"Global lexical index initialized with {len(_index.entries)} items")

    # Clear query expansion cache since vocabulary has changed
    try:
        from app.services import query_expansion
        query_expansion.clear_cache()
    except Exception:
        pass  # If query_expansion not available yet, that's fine
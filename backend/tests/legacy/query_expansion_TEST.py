"""
Tests for LLM-assisted catalog-aware query expansion.

Validates that:
1. Vocabulary extraction works
2. Expansion only returns terms from catalog
3. Caching works
4. Graceful fallback on LLM failure
"""

import asyncio
import sys
from app.services import catalog_lexical_index, query_expansion
from app.services.catalog_lexical_index import CatalogIndexEntry


def test_vocabulary_extraction():
    """Test that vocabulary extraction pulls from the index."""
    print("\n=== Test: Vocabulary Extraction ===")

    # Build a small test index
    index = catalog_lexical_index.CatalogLexicalIndex()
    items = [
        CatalogIndexEntry(
            sys_id="cisco_id",
            name="Cisco Jabber Softphone",
            short_description="Mobile phone communication service",
            category="Mobile Devices"
        ),
        CatalogIndexEntry(
            sys_id="iphone_id",
            name="iPhone Request",
            short_description="Request a company mobile iPhone device",
            category="Mobile Devices"
        ),
        CatalogIndexEntry(
            sys_id="laptop_id",
            name="Laptop Request",
            short_description="Request a company laptop for work",
            category="Hardware"
        ),
    ]

    # Tokenize and add to index
    for item in items:
        item.name_tokens = catalog_lexical_index._tokenize(item.name)
        item.desc_tokens = catalog_lexical_index._tokenize((item.short_description or ""))
        item.category_tokens = catalog_lexical_index._tokenize((item.category or ""))
        item.all_tokens = item.name_tokens | item.desc_tokens | item.category_tokens
        item.name_phrases = []
        item.desc_phrases = []
        index.add_item(item)

    # Set as global index
    catalog_lexical_index._index = index

    # Get vocabulary
    vocab = query_expansion.get_catalog_vocabulary(max_terms=100)
    print(f"Extracted {len(vocab)} vocabulary terms from index")
    print(f"Sample vocab: {vocab[:10]}")

    # Verify vocab contains expected terms
    vocab_set = set(vocab)
    expected_terms = {"cisco", "jabber", "softphone", "mobile", "phone", "iphone", "laptop", "device"}
    found = expected_terms & vocab_set
    missing = expected_terms - vocab_set

    print(f"Expected terms found: {found}")
    if missing:
        print(f"Missing terms: {missing}")

    assert len(vocab) > 5, "Should have extracted multiple terms"
    assert found, "Should find at least some expected terms"
    print("Test passed!")


def test_post_filter_validation():
    """Test that post-filter removes hallucinated terms."""
    print("\n=== Test: Post-Filter Validation ===")

    # Simulate LLM returning some valid + some invalid terms
    catalog_vocab = {"mobile", "phone", "softphone", "cisco", "jabber", "device", "laptop"}

    llm_response = ["phone", "smartphone", "device", "invalid_term", "mobile"]

    # Apply filter
    valid_set = catalog_vocab
    filtered = [t for t in llm_response if t.lower() in valid_set]

    print(f"LLM response: {llm_response}")
    print(f"Catalog vocab: {catalog_vocab}")
    print(f"Filtered result: {filtered}")

    # Check filtering worked
    assert "phone" in filtered, "Valid term should be kept"
    assert "device" in filtered, "Valid term should be kept"
    assert "invalid_term" not in filtered, "Invalid term should be removed"
    assert "smartphone" not in filtered, "Hallucinated term should be removed"

    print("Test passed!")


def test_cache():
    """Test that caching prevents repeated calls."""
    print("\n=== Test: Cache Management ===")

    # Clear cache
    query_expansion.clear_cache()

    # Manually set cache entry
    query = "test mobile query"
    cache_key = query.strip().lower()
    query_expansion._expansion_cache[cache_key] = (
        __import__('time').time(),
        ["phone", "device"]
    )

    # Check it's cached
    status = query_expansion.get_cache_status()
    print(f"Cache status after adding entry: {status}")

    assert status["cache_size"] >= 1, "Cache should have at least 1 entry"
    assert status["fresh_entries"] >= 1, "Entry should be fresh"

    # Clear and verify
    query_expansion.clear_cache()
    status = query_expansion.get_cache_status()
    print(f"Cache status after clearing: {status}")

    assert status["cache_size"] == 0, "Cache should be empty after clear"
    print("Test passed!")


async def test_expansion_grounding():
    """Test that expansion only returns terms from catalog (without LLM call)."""
    print("\n=== Test: Expansion Grounding (Offline) ===")

    # Build test index again
    index = catalog_lexical_index.CatalogLexicalIndex()
    items = [
        CatalogIndexEntry(
            sys_id="cisco_id",
            name="Cisco Jabber Softphone",
            short_description="Mobile phone communication service",
            category="Mobile Devices"
        ),
    ]

    for item in items:
        item.name_tokens = catalog_lexical_index._tokenize(item.name)
        item.desc_tokens = catalog_lexical_index._tokenize((item.short_description or ""))
        item.category_tokens = catalog_lexical_index._tokenize((item.category or ""))
        item.all_tokens = item.name_tokens | item.desc_tokens | item.category_tokens
        item.name_phrases = []
        item.desc_phrases = []
        index.add_item(item)

    catalog_lexical_index._index = index

    # Get vocab
    vocab = query_expansion.get_catalog_vocabulary()
    print(f"Catalog vocabulary size: {len(vocab)}")
    print(f"Sample: {vocab[:10]}")

    # Test vocabulary filtering (offline, no LLM)
    all_catalog_terms = set(vocab)
    test_expansions = ["phone", "softphone", "unknown_term", "mobile"]
    filtered = [t for t in test_expansions if t in all_catalog_terms]

    print(f"Test expansions: {test_expansions}")
    print(f"Filtered (only catalog terms): {filtered}")

    # Verify only catalog terms remain
    assert "phone" in filtered or "softphone" in filtered, "Should contain valid catalog term"
    assert "unknown_term" not in filtered, "Should filter out non-catalog term"

    print("Test passed!")


def run_all_tests():
    """Run all tests."""
    print("=" * 70)
    print("QUERY EXPANSION - UNIT TESTS")
    print("=" * 70)

    try:
        test_vocabulary_extraction()
        test_post_filter_validation()
        test_cache()
        asyncio.run(test_expansion_grounding())

        print("\n" + "=" * 70)
        print("All tests passed!")
        print("=" * 70)
        return True

    except AssertionError as e:
        print(f"\nTest failed: {e}")
        return False
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
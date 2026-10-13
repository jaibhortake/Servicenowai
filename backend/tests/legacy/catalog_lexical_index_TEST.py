"""
Unit tests and examples for the catalog lexical indexing system.

Demonstrates the system working with real-world examples,
including the "I need a mobile" → "Cisco Jabber Softphone" case from the screenshot.
"""

import sys
from app.services import catalog_lexical_index
from app.services.catalog_lexical_index import (
    CatalogIndexEntry, CatalogLexicalIndex,
    _tokenize, _extract_phrases, _normalize_query, _fuzzy_token_match
)


def test_normalization():
    """Test query normalization."""
    print("\n=== Test: Query Normalization ===")

    tests = [
        ("I need a mobile", "i need a mobile"),
        ("I need a MOBILE!!!!", "i need a mobile"),
        ("Laptop   for   development", "laptop for development"),
    ]

    for input_q, expected in tests:
        result = _normalize_query(input_q)
        status = "✓" if result == expected else "✗"
        print(f"{status} '{input_q}' → '{result}' (expected '{expected}')")


def test_tokenization():
    """Test tokenization."""
    print("\n=== Test: Tokenization ===")

    tests = [
        ("I need a mobile", {"need", "mobile"}),
        ("Cisco jabber softphone mobile device", {"cisco", "jabber", "softphone", "mobile", "device"}),
        ("the quick brown fox", {"quick", "brown", "fox"}),  # "the" is stopword
    ]

    for input_text, expected_tokens in tests:
        result = _tokenize(input_text)
        status = "✓" if result == expected_tokens else "✗"
        print(f"{status} '{input_text}'")
        print(f"   Got: {result}")
        print(f"   Expected: {expected_tokens}")


def test_phrase_extraction():
    """Test phrase extraction."""
    print("\n=== Test: Phrase Extraction ===")

    text = "Cisco Jabber Softphone for Mobile Communication"
    phrases = _extract_phrases(text, length=2)
    print(f"Text: '{text}'")
    print(f"2-word phrases: {phrases}")


def test_fuzzy_matching():
    """Test fuzzy token matching."""
    print("\n=== Test: Fuzzy Matching ===")

    candidates = ["mobile", "softphone", "cisco", "device", "notebook", "laptop"]

    tests = [
        ("mobile", 0.75),     # Should match "mobile"
        ("mobil", 0.75),      # Typo - should match "mobile"
        ("laptop", 0.75),     # Should match "laptop"
        ("loptop", 0.75),     # Typo - should match "laptop"
        ("phone", 0.75),      # Should match "softphone"
    ]

    for query_token, threshold in tests:
        matches = _fuzzy_token_match(query_token, candidates, threshold=threshold)
        print(f"Query: '{query_token}' (threshold={threshold}) → {matches}")


def test_mobile_request_example():
    """
    Test the real-world example from the screenshot:
    "I need a mobile" should match "Cisco Jabber Softphone"
    """
    print("\n=== Test: 'I need a mobile' Example ===")

    # Create a small test catalog
    catalog_items = [
        CatalogIndexEntry(
            sys_id="cisco_jabber_id",
            name="Cisco Jabber Softphone",
            short_description="Mobile phone communication and conferencing service for remote work",
            description="Enterprise VoIP softphone for calls, video, and messaging. Supports mobile devices.",
            category="Mobile Devices"
        ),
        CatalogIndexEntry(
            sys_id="paper_supplies_id",
            name="Paper and Supplies",
            short_description="Order office supplies such as paper, stationery and computer supplies",
            description="Stock office essentials including paper, pens, folders, and more.",
            category="Office Supplies"
        ),
        CatalogIndexEntry(
            sys_id="iphone_request_id",
            name="iPhone Request",
            short_description="Request a company iPhone device",
            description="Request an Apple iPhone for work use. Supports various models.",
            category="Mobile Devices"
        ),
    ]

    # Build index
    index = CatalogLexicalIndex()
    for item in catalog_items:
        item.name_tokens = _tokenize(item.name)
        item.desc_tokens = _tokenize(item.short_description or "") | _tokenize(item.description or "")
        item.category_tokens = _tokenize(item.category or "")
        item.all_tokens = item.name_tokens | item.desc_tokens | item.category_tokens
        item.name_phrases = _extract_phrases(item.name, length=2)
        item.desc_phrases = _extract_phrases(item.short_description or "", length=2)
        index.add_item(item)

    print(f"Index built with {len(index.entries)} items")

    # Search
    query = "I need a mobile"
    results = index.search(query)

    print(f"\nQuery: '{query}'")
    print(f"Results ({len(results)} items):")
    for sys_id, score in results:
        entry = index.entries[sys_id]
        print(f"  {entry.name}")
        print(f"    sys_id: {sys_id}")
        print(f"    score: {score:.3f}")
        print(f"    category: {entry.category}")

    # Assertions
    assert len(results) >= 2, "Should match at least 2 items (Cisco Jabber and iPhone)"
    assert results[0][0] == "cisco_jabber_id", "Top result should be Cisco Jabber"
    assert results[0][1] >= 0.45, f"Top score should be >= 0.45, got {results[0][1]}"

    print("\n✓ Test passed: Correct item ranked first with sufficient confidence")


def test_laptop_development_example():
    """
    Test: "laptop for development"
    """
    print("\n=== Test: 'Laptop for Development' Example ===")

    catalog_items = [
        CatalogIndexEntry(
            sys_id="dell_laptop_id",
            name="Dell Laptop for Developers",
            short_description="High-performance Dell laptop optimized for software development",
            description="Dell XPS laptop with powerful CPU, GPU, and development tools pre-installed",
            category="Hardware"
        ),
        CatalogIndexEntry(
            sys_id="monitor_id",
            name="4K Monitor",
            short_description="Monitor for workstations",
            description="High resolution display device",
            category="Hardware"
        ),
    ]

    index = CatalogLexicalIndex()
    for item in catalog_items:
        item.name_tokens = _tokenize(item.name)
        item.desc_tokens = _tokenize(item.short_description or "") | _tokenize(item.description or "")
        item.category_tokens = _tokenize(item.category or "")
        item.all_tokens = item.name_tokens | item.desc_tokens | item.category_tokens
        item.name_phrases = _extract_phrases(item.name, length=2)
        item.desc_phrases = _extract_phrases(item.short_description or "", length=2)
        index.add_item(item)

    query = "laptop for development"
    results = index.search(query)

    print(f"Query: '{query}'")
    print(f"Results ({len(results)} items):")
    for sys_id, score in results:
        entry = index.entries[sys_id]
        print(f"  {entry.name} (score: {score:.3f})")

    assert len(results) >= 1, "Should match Dell laptop"
    assert results[0][0] == "dell_laptop_id", "Top result should be Dell laptop"

    print("✓ Test passed")


def test_vpn_access_example():
    """
    Test: "I need VPN access"
    """
    print("\n=== Test: 'I need VPN access' Example ===")

    catalog_items = [
        CatalogIndexEntry(
            sys_id="vpn_request_id",
            name="VPN Access Request",
            short_description="Request remote access via Virtual Private Network",
            description="Set up secure VPN connection for remote work and off-site access",
            category="Access"
        ),
        CatalogIndexEntry(
            sys_id="cisco_vpn_id",
            name="Cisco AnyConnect VPN",
            short_description="Cisco enterprise VPN solution",
            description="VPN software for secure remote connectivity",
            category="Security"
        ),
        CatalogIndexEntry(
            sys_id="laptop_id",
            name="Laptop Request",
            short_description="Request a company laptop",
            description="Standard laptop for work",
            category="Hardware"
        ),
    ]

    index = CatalogLexicalIndex()
    for item in catalog_items:
        item.name_tokens = _tokenize(item.name)
        item.desc_tokens = _tokenize(item.short_description or "") | _tokenize(item.description or "")
        item.category_tokens = _tokenize(item.category or "")
        item.all_tokens = item.name_tokens | item.desc_tokens | item.category_tokens
        item.name_phrases = _extract_phrases(item.name, length=2)
        item.desc_phrases = _extract_phrases(item.short_description or "", length=2)
        index.add_item(item)

    query = "I need VPN access"
    results = index.search(query)

    print(f"Query: '{query}'")
    print(f"Results ({len(results)} items):")
    for sys_id, score in results:
        entry = index.entries[sys_id]
        print(f"  {entry.name} (score: {score:.3f})")

    assert len(results) >= 2, "Should match VPN-related items"
    assert results[0][0] == "vpn_request_id", "Top result should be VPN Access Request"

    print("✓ Test passed")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("CATALOG LEXICAL INDEX - UNIT TESTS")
    print("=" * 60)

    try:
        test_normalization()
        test_tokenization()
        test_phrase_extraction()
        test_fuzzy_matching()
        test_mobile_request_example()
        test_laptop_development_example()
        test_vpn_access_example()

        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return True

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return False
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
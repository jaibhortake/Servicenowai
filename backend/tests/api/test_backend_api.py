#!/usr/bin/env python
"""
Test the backend API endpoints
"""

import requests
import json
import time

BASE_URL = "http://localhost:8099"


def test_health():
    """Test health endpoint"""
    print("\n" + "=" * 80)
    print("Testing health endpoint")
    print("=" * 80)
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


def test_mcp_status():
    """Test MCP status endpoint"""
    print("\n" + "=" * 80)
    print("Testing MCP status endpoint")
    print("=" * 80)
    try:
        response = requests.get(f"{BASE_URL}/api/mcp/status", timeout=5)
        print(f"Status: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


def test_interpret(query):
    """Test interpret endpoint"""
    print("\n" + "=" * 80)
    print(f"Testing interpret endpoint with query: '{query}'")
    print("=" * 80)
    try:
        payload = {"text": query}
        start_time = time.time()
        response = requests.post(f"{BASE_URL}/api/interpret", json=payload, timeout=60)
        elapsed = time.time() - start_time

        print(f"Status: {response.status_code}")
        print(f"Response time: {elapsed:.2f}s")

        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ INTERPRET RESULT:")
            print(f"  Intent: {data.get('intent')}")
            print(f"  Confidence: {data.get('confidence')}")
            print(f"  Ticket Type: {data.get('ticket_type')}")

            catalogs = data.get("suggested_catalogs", [])
            print(f"\n✅ CATALOG RESULTS: {len(catalogs)} items found")

            if catalogs:
                print("\n📋 TOP 5 RESULTS:")
                for i, catalog in enumerate(catalogs[:5], 1):
                    print(
                        f"  {i}. {catalog.get('name')} (score: {catalog.get('relevance_score')})"
                    )

            return True
        else:
            print(f"Error: {response.text}")
            return False

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("BACKEND API TEST SUITE")
    print("=" * 80)

    # Test health
    if not test_health():
        print("\n❌ Backend is not running. Please start it first.")
        exit(1)

    # Test MCP status
    test_mcp_status()

    # Test interpret with sample queries
    test_queries = [
        "I need a mobile device",
        "I want to request a laptop",
        "Can I get VPN access?",
    ]

    for query in test_queries:
        test_interpret(query)
        time.sleep(1)  # Small delay between requests

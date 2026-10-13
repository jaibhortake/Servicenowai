#!/usr/bin/env python
"""
Test the MCP flow: LLM keywords extraction -> MCP search -> results
"""
import asyncio
import json
import sys
from app.services import ai_service

async def test_interpret_and_search():
    test_queries = [
        "I need a mobile device",
        "I want to request a laptop",
        "Can I get VPN access?",
        "I need office supplies",
    ]

    print("\n" + "="*80)
    print("TESTING MCP FLOW: LLM Keywords Extraction -> MCP Search")
    print("="*80)

    for query in test_queries:
        print(f"\n{'='*80}")
        print(f"Testing query: {query}")
        print('='*80)

        try:
            result = await ai_service.interpret_request_async(query)

            print(f"\n✅ INTERPRET RESULT:")
            print(f"  Intent: {result.get('intent')}")
            print(f"  Confidence: {result.get('confidence')}")
            print(f"  Ticket Type: {result.get('ticket_type')}")
            print(f"  Search Keywords: {result.get('search_keywords')}")

            catalogs = result.get('suggested_catalogs', [])
            print(f"\n✅ CATALOG SEARCH RESULTS: {len(catalogs)} items found")

            if catalogs:
                print("\n📋 TOP 5 RESULTS:")
                for i, catalog in enumerate(catalogs[:5], 1):
                    print(f"  {i}. {catalog.get('name')} (score: {catalog.get('relevance_score')})")
                    if catalog.get('match_reason'):
                        print(f"     Reason: {catalog.get('match_reason')}")
            else:
                print("  ⚠️  No catalog items found")

        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_interpret_and_search())

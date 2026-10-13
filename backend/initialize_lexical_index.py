#!/usr/bin/env python
"""
Manual lexical index initialization script.

Run this if the index didn't initialize at backend startup.
"""

import sys
import asyncio
sys.path.insert(0, '.')

async def main():
    print("=" * 70)
    print("CATALOG LEXICAL INDEX - MANUAL INITIALIZATION")
    print("=" * 70)

    from app.services import catalog_startup

    print("\nInitializing lexical index from ServiceNow...")
    print("This may take 30-60 seconds depending on catalog size...")
    print()

    success = await catalog_startup.initialize_catalog_index_async(limit=500, timeout=60)

    if success:
        print("\n" + "=" * 70)
        print("SUCCESS - Lexical Index Initialized!")
        print("=" * 70)

        # Show status
        status = catalog_startup.get_index_status()
        print(f"\nIndex Status:")
        print(f"  Catalog items: {status['catalog_items']}")
        print(f"  Unique tokens: {status['tokens']}")
        print(f"  Phrases: {status['phrases']}")
        print(f"  Categories: {status['categories']}")

        # Test search
        from app.services import catalog_lexical_index
        print(f"\nTesting search for 'mobile'...")
        results = catalog_lexical_index.search_index("mobile")
        print(f"  Found {len(results)} results:")
        for r in results[:5]:
            print(f"    - {r['name']}: {r['score']:.3f}")

        print("\n" + "=" * 70)
        print("The system is ready! Query expansion will now work.")
        print("=" * 70)
        return 0
    else:
        print("\n" + "=" * 70)
        print("ERROR - Index initialization failed")
        print("=" * 70)
        print("\nPossible causes:")
        print("  1. ServiceNow API unreachable")
        print("  2. MCP server not responding")
        print("  3. No catalog items available")
        print("\nCheck:")
        print("  - ServiceNow instance URL is correct")
        print("  - Credentials are valid")
        print("  - MCP server is running (if configured)")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
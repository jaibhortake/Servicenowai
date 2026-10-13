# Catalog Lexical Index - Examples & Design

## Overview

The lexical indexing system provides **deterministic, embedding-free catalog retrieval** by building an inverted index directly from catalog metadata.

### Key Design Principles

1. **No Embeddings** - Uses pure lexical matching (tokens, phrases, edit distance)
2. **Catalog-Derived** - Index built 100% from catalog data (name, description, category)
3. **Deterministic** - Same input always produces same output (no LLM variance)
4. **Fast** - Pre-built inverted index enables O(1) token lookup
5. **Transparent** - Scoring is interpretable and explainable

---

## Example: "I need a mobile"

### Input
```
Query: "I need a mobile"
```

### Processing

#### Step 1: Query Normalization
```
Original:  "I need a mobile"
Normalized: "i need a mobile"
Stripped punctuation, extra spaces: "i need a mobile"
```

#### Step 2: Query Tokenization
```
Tokens: ["need", "mobile"]  
(removed stopword "i", short words filtered)

Phrases: ["need mobile"]  (2-word phrase)
```

#### Step 3: Index Lookup

Suppose the catalog contains:
```
Item 1: Cisco Jabber Softphone (Mobile Devices)
  - Name tokens: {cisco, jabber, softphone, mobile, devices}
  - Desc tokens: {mobile, phone, communication, device}
  - Category tokens: {mobile, devices, hardware}

Item 2: Paper and Supplies (Office Supplies)
  - Name tokens: {paper, supplies}
  - Desc tokens: {office, paper, stationery, supplies}
  - Category tokens: {office, supplies}

Item 3: iPhone Request (Mobile Devices)
  - Name tokens: {iphone, request}
  - Desc tokens: {mobile, phone, ios, device}
  - Category tokens: {mobile, devices, hardware}
```

Lexical index token map:
```
"mobile" → {Item1.sys_id, Item3.sys_id}
"phone" → {Item1.sys_id, Item3.sys_id}
"cisco" → {Item1.sys_id}
"softphone" → {Item1.sys_id}
```

#### Step 4: Find Candidates
Query token "mobile" matches:
- Item 1 (Cisco Jabber) - token in name + category
- Item 3 (iPhone) - token in name + category

#### Step 5: Score Candidates

**Item 1: Cisco Jabber Softphone**
```
Exact phrase matches in name: 0
Token overlap in name: 1/2 = 50%
  - "mobile" found in name_tokens ✓
  Score += 0.50 * 0.50 = 0.25

Token overlap in description: 1/2 = 50%
  - "mobile" found in desc_tokens ✓
  Score += 0.50 * 0.25 = 0.125

Category match: 1/2
  - "mobile" found in category_tokens ✓
  Score += 0.10

Fuzzy matches: "mobile" ~= "mobile" (exact)
  Score += 0.05

TOTAL SCORE = 0.25 + 0.125 + 0.10 + 0.05 = 0.525
```

**Item 3: iPhone Request**
```
Similar calculation...
TOTAL SCORE ≈ 0.50 (slightly lower due to "request" noise)
```

**Item 2: Paper and Supplies**
```
Token "mobile" not found anywhere
SCORE = 0.0 (excluded from results)
```

#### Step 6: Return Results
```json
[
  {
    "sys_id": "item1_id",
    "name": "Cisco Jabber Softphone",
    "score": 0.525,
    "short_description": "Mobile phone communication service"
  },
  {
    "sys_id": "item3_id",
    "name": "iPhone Request",
    "score": 0.50,
    "short_description": "Request a company iPhone device"
  }
]
```

### Result
✅ **Correct item selected**: Cisco Jabber Softphone  
✅ **Relevance Score**: 0.525 (above 0.45 threshold, clear match)  
✅ **Why it matched**:
- Query token "mobile" found in name
- Query token "mobile" found in category
- Item describes mobile phone services

---

## Ranking Algorithm Details

### Scoring Formula

```
score = 0
score += exact_phrase_matches_in_name * 0.25  (max 0.80)
score += (name_token_overlap / query_tokens) * 0.50
score += (desc_token_overlap / query_tokens) * 0.25
score += (category_match ? 0.10 : 0.0)
score += fuzzy_matches * 0.05  (per fuzzy match)
score = clamp(score, 0.0, 1.0)
```

### Ranking Factors (Priority Order)

1. **Exact Phrase Match in Name** (Highest Priority)
   - 2-word phrases from query matching name phrases
   - Example: "mobile phone" in name = +0.25

2. **Token Overlap in Name** (High Priority)
   - Query tokens found in item name
   - Coverage-based: (overlap / max(query_size, name_size)) * 0.50
   - Example: "mobile" in name = +0.25

3. **Token Overlap in Description** (Medium Priority)
   - Query tokens in short_description or description
   - Coverage-based: (overlap / query_size) * 0.25
   - Example: "mobile" in description = +0.125

4. **Category Match** (Medium Priority)
   - Query tokens found in category field
   - Flat bonus: +0.10

5. **Fuzzy Token Match** (Low Priority)
   - Tokens similar to query (edit distance >= 0.80)
   - In name: +0.05 per match
   - In description: +0.02 per match

### Example Scoring Breakdown

Query: "laptop for development"

```
Tokens: {laptop, development}
Phrases: ["laptop development"]

Candidate: "Dell Laptop for Developers"
  name_tokens: {dell, laptop, developers}
  desc_tokens: {laptop, development, machine, powerful}

Scoring:
  Phrase "laptop development" in name_phrases? No → 0
  Token "laptop" in name_tokens? Yes → 1/2 * 0.50 = 0.25
  Token "development" in name_tokens? No
  Token "development" in desc_tokens? Yes → 1/2 * 0.25 = 0.125
  Category match? Maybe → 0.10
  Fuzzy: "developers" ~= "development" (0.81) → 0.05

  TOTAL = 0.25 + 0.125 + 0.10 + 0.05 = 0.525
```

---

## Integration: Hybrid Mode (LLM + Lexical)

The system combines lexical indexing with OpenAI ranking for best of both worlds:

```
User Query
    ↓
[LEXICAL] Fast candidate filtering (0-20ms)
    ↓
[LLM] Semantic ranking of candidates (200-500ms)
    ↓
[VALIDATION] Lexical scores detect ranking anomalies
    ↓
Ranked Results
```

### When to Use Which

| Scenario | Use |
|----------|-----|
| LLM available, query is complex | Hybrid (LLM primary + lexical fallback) |
| LLM unavailable, query is simple | Lexical only |
| Need fast response time | Lexical only |
| Need semantic understanding | LLM only |
| Need deterministic results | Lexical only |
| Building production search | Hybrid |

---

## Fuzzy Matching Examples

The system uses SequenceMatcher for fuzzy token matching:

```
Query: "mobil"
Candidate: "mobile"
Ratio: 5/6 = 0.833 ≥ 0.75 threshold → MATCH ✓

Query: "developement" (typo)
Candidate: "development"
Ratio: 10/11 = 0.909 ≥ 0.75 threshold → MATCH ✓

Query: "laptop"
Candidate: "workstation"
Ratio: 4/9 = 0.444 < 0.75 threshold → NO MATCH ✗
```

---

## Stopwords & Exclusions

Tokens filtered from index:

Common articles/prepositions:
```
{a, an, and, are, as, at, be, by, for, from, has, he, in, is, it, 
 its, of, on, or, that, the, to, was, will, with}
```

Common service noise:
```
{request, service, catalog, item, access, provide, get}
```

Short tokens (< 3 chars):
```
Excluded: a, it, on, by, ...
Included exceptions: {ai, it, ip, vm, os}
```

---

## Building & Maintaining the Index

### Initialization (Startup)

```python
from app.services import catalog_lexical_index, catalog_retrieval_hybrid

# Fetch all catalog items from ServiceNow
items = snow.list_catalog_items(limit=5000)

# Build index
catalog_lexical_index.initialize_index(items)
# → Creates inverted index, 5000 token buckets, phrase index
# → Index ready for O(1) lookups
```

### Incremental Updates

Add new items as they arrive:

```python
new_item = {
    "sys_id": "new_id",
    "name": "New Catalog Item",
    "short_description": "...",
    "category": "hardware"
}

index = catalog_lexical_index.get_index()
index.add_item(CatalogIndexEntry(...))
```

### Periodic Rebuilds

Rebuild index nightly or weekly to stay fresh:

```python
# At startup or on schedule
catalog_retrieval_hybrid.rebuild_index_from_servicenow(limit=5000)
```

---

## Performance Characteristics

### Query Performance

```
Lookup operation: O(q) where q = number of query tokens
  - Token lookup in inverted index: O(1) per token
  - Candidate collection: O(c) where c = candidate items
  - Scoring candidates: O(c * t) where t = avg tokens per item

Typical: 2-5 token queries → 10-50 candidates → 5-20ms total
```

### Index Size

```
Typical ServiceNow catalog: 5000 items
Token index: ~8000-12000 unique tokens
Phrase index: ~3000-5000 unique phrases
Memory usage: ~5-10 MB for typical catalog
```

### Scaling

```
1000 items:  ~1ms lookup
5000 items:  ~3ms lookup
10000 items: ~5ms lookup
```

---

## Future Enhancements

1. **Synonym Expansion from Catalog**
   - Learn that "laptop" == "notebook" from actual catalog items
   - Build synonym graph from related categories

2. **Temporal Scoring**
   - Recent catalog updates weighted higher
   - Deprecations weighted lower

3. **User History**
   - Track which items users selected
   - Boost items with positive interaction history

4. **A/B Testing**
   - Compare lexical vs LLM vs hybrid results
   - Optimize thresholds based on user feedback

5. **Multi-Language Support**
   - Tokenize and index in multiple languages
   - Support queries in user's native language
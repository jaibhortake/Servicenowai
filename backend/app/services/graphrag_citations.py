from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote


_INCIDENT_NUMBER_RE = re.compile(r"\bINC\d+\b", re.IGNORECASE)
_CORE_RECORD_NUMBER_RE = re.compile(r"\b(?:INC|REQ|RITM|CHG|PRB)\d+\b", re.IGNORECASE)
_BRACKETED_INCIDENT_MARKER_RE = re.compile(r"\[(INC\d+)\]", re.IGNORECASE)
_BRACKETED_CORE_RECORD_MARKER_RE = re.compile(r"\[((?:INC|REQ|RITM|CHG|PRB)\d+)\]", re.IGNORECASE)

_NUMBER_PREFIX_TABLES = {
    "INC": ("incident", "incident", "Incident"),
    "PRB": ("problem", "problem", "Problem"),
    "REQ": ("service_request", "sc_request", "Request"),
    "RITM": ("request_item", "sc_req_item", "Requested item"),
    "CHG": ("change_request", "change_request", "Change request"),
}

_GRAPH_LABEL_TABLES = {
    "Incident": ("incident", "incident", "Incident"),
    "Problem": ("problem", "problem", "Problem"),
    "ServiceRequest": ("service_request", "sc_request", "Request"),
    "RequestItem": ("request_item", "sc_req_item", "Requested item"),
    "ChangeRequest": ("change_request", "change_request", "Change request"),
    "CatalogItem": ("catalog_item", "sc_cat_item", "Catalog item"),
    "Appliance": ("appliance", "cmdb_ci", "Appliance"),
}


def build_servicenow_incident_url(
    instance_url: str,
    *,
    sys_id: Optional[str] = None,
    number: Optional[str] = None,
) -> str:
    base_url = str(instance_url or "").strip().rstrip("/")
    if not base_url:
        return ""

    normalized_sys_id = _string_value(sys_id)
    if normalized_sys_id:
        return f"{base_url}/incident.do?sys_id={quote(normalized_sys_id, safe='')}"

    normalized_number = _incident_number(number)
    if normalized_number:
        query = quote(f"number={normalized_number}", safe="")
        return f"{base_url}/incident_list.do?sysparm_query={query}"

    return ""


def build_servicenow_record_url(
    instance_url: str,
    *,
    table: str,
    sys_id: Optional[str] = None,
    number: Optional[str] = None,
    name: Optional[str] = None,
    query_field: str = "name",
) -> str:
    base_url = str(instance_url or "").strip().rstrip("/")
    normalized_table = _string_value(table)
    if not base_url or not normalized_table:
        return ""

    normalized_sys_id = _string_value(sys_id)
    if normalized_sys_id:
        return f"{base_url}/{normalized_table}.do?sys_id={quote(normalized_sys_id, safe='')}"

    normalized_number = _string_value(number)
    if normalized_number:
        query = quote(f"number={normalized_number}", safe="")
        return f"{base_url}/{normalized_table}_list.do?sysparm_query={query}"

    normalized_name = _string_value(name)
    normalized_query_field = _string_value(query_field) or "name"
    if normalized_name:
        query = quote(f"{normalized_query_field}LIKE{normalized_name}", safe="")
        return f"{base_url}/{normalized_table}_list.do?sysparm_query={query}"

    return ""


def extract_record_citations(
    result: Dict[str, Any],
    *,
    instance_url: str,
    scope: str = "incidents",
    limit: int = 12,
) -> List[Dict[str, str]]:
    if str(scope or "").strip().lower() != "core_records":
        return extract_incident_citations(result, instance_url=instance_url, limit=limit)

    answer_text = _answer_text(result)
    answer_numbers = _answer_record_numbers(result)
    citations: List[Dict[str, str]] = []

    if answer_numbers:
        citations_by_number = _record_citations_by_number(
            _iter_evidence_rows(result),
            instance_url=instance_url,
        )
        for number in answer_numbers:
            citation = citations_by_number.get(number) or _record_card_from_number(
                number,
                instance_url=instance_url,
            )
            if citation:
                citations.append(citation)
            if len(citations) >= limit:
                return _dedupe_card_list(citations, limit=limit)

    for row in _iter_result_rows(result):
        for citation in _record_cards_from_row(row, instance_url=instance_url):
            if not _card_is_allowed(citation):
                continue
            if not _card_referenced_in_answer(citation, answer_text):
                continue
            citations.append(citation)
            if len(_dedupe_card_list(citations, limit=limit)) >= limit:
                return _sort_cards_by_answer_order(
                    _dedupe_card_list(citations, limit=limit),
                    answer_text,
                )

    return _sort_cards_by_answer_order(
        _dedupe_card_list(citations, limit=limit),
        answer_text,
    )


def extract_incident_citations(
    result: Dict[str, Any],
    *,
    instance_url: str,
    limit: int = 12,
) -> List[Dict[str, str]]:
    answer_numbers = _answer_incident_numbers(result)
    if answer_numbers:
        citations_by_number = _citations_by_number(
            _iter_evidence_rows(result),
            instance_url=instance_url,
        )
        citations: List[Dict[str, str]] = []
        for number in answer_numbers:
            citation = citations_by_number.get(number) or _citation_from_number(
                number,
                instance_url=instance_url,
            )
            if citation:
                citations.append(citation)
            if len(citations) >= limit:
                break
        return citations

    return _dedupe_citations(
        _iter_result_rows(result),
        instance_url=instance_url,
        limit=limit,
    )


def strip_answer_citation_markers(answer: Any) -> str:
    text = _string_value(answer)
    if not text:
        return ""

    text = _BRACKETED_CORE_RECORD_MARKER_RE.sub("", text)
    text = re.sub(
        r"\s*(?:supported by|cited (?:incidents?|records?))[\s\.,;:]*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+([,.;:!?])", r"\1", line)
        line = re.sub(r"(?:,\s*){2,}", ", ", line)
        line = re.sub(r",\s*(?:and|or)\s*([.;:!?])", r"\1", line, flags=re.IGNORECASE)
        line = re.sub(r"\b(?:and|or)\s*([.;:!?])", r"\1", line, flags=re.IGNORECASE)
        line = re.sub(r",\s*([.;:!?])", r"\1", line)
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _answer_incident_numbers(result: Dict[str, Any]) -> List[str]:
    return [number for number in _answer_record_numbers(result) if number.startswith("INC")]


def _answer_record_numbers(result: Dict[str, Any]) -> List[str]:
    answer = _answer_text(result)
    if not answer:
        return []

    numbers: List[str] = []
    seen = set()
    for match in _CORE_RECORD_NUMBER_RE.finditer(answer):
        number = match.group(0).upper()
        if number in seen:
            continue
        seen.add(number)
        numbers.append(number)
    return numbers


def _answer_text(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""

    answer = _string_value(result.get("answer"))
    if not answer:
        fallback = result.get("fallback_result")
        if isinstance(fallback, dict):
            answer = _string_value(fallback.get("answer"))
    return answer


def _record_citations_by_number(
    rows: Iterable[Dict[str, Any]],
    *,
    instance_url: str,
) -> Dict[str, Dict[str, str]]:
    citations_by_number: Dict[str, Dict[str, str]] = {}
    for row in rows:
        for citation in _record_cards_from_row(row, instance_url=instance_url):
            number = citation.get("number", "").strip().upper()
            if number and number not in citations_by_number:
                citations_by_number[number] = citation
    return citations_by_number


def _citations_by_number(
    rows: Iterable[Dict[str, Any]],
    *,
    instance_url: str,
) -> Dict[str, Dict[str, str]]:
    citations_by_number: Dict[str, Dict[str, str]] = {}
    for row in rows:
        citation = _citation_from_row(row, instance_url=instance_url)
        if not citation:
            continue
        number = citation.get("number", "").strip().upper()
        if number and number not in citations_by_number:
            citations_by_number[number] = citation
    return citations_by_number


def _dedupe_citations(
    rows: Iterable[Dict[str, Any]],
    *,
    instance_url: str,
    limit: int,
) -> List[Dict[str, str]]:
    citations: List[Dict[str, str]] = []
    seen = set()
    for row in rows:
        citation = _citation_from_row(row, instance_url=instance_url)
        if not citation:
            continue
        key = citation.get("number", "").strip().upper() or citation.get("sys_id", "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        citations.append(citation)
        if len(citations) >= limit:
            return citations
    return citations


def _dedupe_card_list(cards: Iterable[Dict[str, str]], *, limit: int) -> List[Dict[str, str]]:
    citations: List[Dict[str, str]] = []
    seen = set()
    for citation in cards:
        key = _card_key(citation)
        if not key or key in seen:
            continue
        seen.add(key)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return citations


def _sort_cards_by_answer_order(cards: List[Dict[str, str]], answer_text: str) -> List[Dict[str, str]]:
    return sorted(
        cards,
        key=lambda card: _card_answer_position(card, answer_text),
    )


def _card_answer_position(card: Dict[str, str], answer_text: str) -> int:
    normalized_answer = _normalized_text(answer_text)
    positions = []
    for key in ("number", "name", "short_description"):
        value = _normalized_text(card.get(key))
        if value:
            index = normalized_answer.find(value)
            if index >= 0:
                positions.append(index)
    return min(positions) if positions else 1_000_000


def _card_is_allowed(card: Dict[str, str]) -> bool:
    return True


def _card_referenced_in_answer(card: Dict[str, str], answer_text: str) -> bool:
    normalized_answer = _normalized_text(answer_text)
    if not normalized_answer:
        return False
    for key in ("number", "name", "short_description"):
        value = _normalized_text(card.get(key))
        if value and value in normalized_answer:
            return True
    if card.get("type") == "problem" or card.get("table") == "problem":
        return _problem_title_terms_match(card.get("short_description"), normalized_answer)
    return False


def _problem_title_terms_match(short_description: Any, normalized_answer: str) -> bool:
    terms = [
        term
        for term in re.findall(r"[a-z0-9]+", _normalized_text(short_description))
        if len(term) >= 4
        and term
        not in {
            "problem",
            "issue",
            "server",
            "cluster",
            "memory",
            "leak",
            "connection",
            "timeout",
            "error",
            "state",
            "states",
        }
    ]
    if not terms:
        return False
    unique_terms = list(dict.fromkeys(terms))
    matches = sum(1 for term in unique_terms if re.search(rf"\b{re.escape(term)}s?\b", normalized_answer))
    return matches >= 2


def _card_key(citation: Dict[str, str]) -> str:
    table = citation.get("table", "").strip().lower()
    sys_id = citation.get("sys_id", "").strip().lower()
    number = citation.get("number", "").strip().upper()
    name = citation.get("name", "").strip().lower()
    if table and sys_id:
        return f"{table}:sys_id:{sys_id}"
    if table and number:
        return f"{table}:number:{number}"
    if table and name:
        return f"{table}:name:{name}"
    return number or sys_id or name


def _iter_evidence_rows(result: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    evidence = result.get("evidence") if isinstance(result, dict) else {}
    yield from _iter_rows_from_evidence(evidence, result_sections_only=False)

    fallback = result.get("fallback_result") if isinstance(result, dict) else {}
    if isinstance(fallback, dict):
        yield from _iter_rows_from_evidence(fallback.get("evidence"), result_sections_only=False)


def _iter_result_rows(result: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    evidence = result.get("evidence") if isinstance(result, dict) else {}
    yield from _iter_rows_from_evidence(evidence, result_sections_only=True)

    fallback = result.get("fallback_result") if isinstance(result, dict) else {}
    if isinstance(fallback, dict):
        yield from _iter_rows_from_evidence(fallback.get("evidence"), result_sections_only=True)


def _iter_rows_from_evidence(evidence: Any, *, result_sections_only: bool) -> Iterable[Dict[str, Any]]:
    if not isinstance(evidence, dict):
        return

    for row in evidence.get("rows") or []:
        if isinstance(row, dict):
            yield row

    for section in evidence.get("executed_sections") or []:
        if not isinstance(section, dict):
            continue
        if result_sections_only and not _is_result_section(section):
            continue
        for row in section.get("rows") or []:
            if isinstance(row, dict):
                yield row


def _is_result_section(section: Dict[str, Any]) -> bool:
    candidate_id = _string_value(section.get("candidate_id")).lower()
    purpose = _string_value(section.get("purpose")).lower()
    source_tokens = (
        "grounding_expansion",
        "vector_hybrid_similarity",
        "grounding candidates",
        "hybrid similarity",
    )
    return not any(token in candidate_id or token in purpose for token in source_tokens)


def _citation_from_row(row: Dict[str, Any], *, instance_url: str) -> Optional[Dict[str, str]]:
    nested_incident = _nested_incident_payload(row)
    number = _incident_number(
        _first_value(
            row,
            (
                "incident_number",
                "incident_no",
                "incident",
                "ticket_number",
                "number",
            ),
        )
    )
    if not number and nested_incident:
        number = _incident_number(
            _first_value(nested_incident, ("incident_number", "number", "ticket_number"))
        )
    if not number:
        return None

    sys_id = _string_value(
        _first_value(
            row,
            (
                "incident_sys_id",
                "incident_id",
                "ticket_sys_id",
                "sys_id",
            ),
        )
    )
    if not sys_id and nested_incident:
        sys_id = _string_value(_first_value(nested_incident, ("incident_sys_id", "sys_id", "incident_id")))
    details = nested_incident or row
    return {
        "type": "incident",
        "table": "incident",
        "number": number,
        "sys_id": sys_id,
        "short_description": _string_value(
            _first_value(details, ("incident_short_description", "short_description", "description", "title"))
        ),
        "state": _string_value(_first_value(details, ("incident_state", "state_display", "state"))),
        "opened_at": _string_value(_first_value(details, ("incident_opened_at", "opened_at"))),
        "url": build_servicenow_incident_url(instance_url, sys_id=sys_id, number=number),
    }


def _record_cards_from_row(row: Dict[str, Any], *, instance_url: str) -> List[Dict[str, str]]:
    cards: List[Dict[str, str]] = []

    for builder in (
        _citation_from_row,
        _problem_card_from_row,
        _change_card_from_row,
        _request_card_from_row,
        _request_item_card_from_row,
        _catalog_item_card_from_row,
        _appliance_card_from_row,
    ):
        card = builder(row, instance_url=instance_url)
        if card:
            cards.append(card)

    for key, value in row.items():
        payload = _as_mapping(value)
        if not payload:
            continue
        labels = _labels_for(value)
        card = _record_card_from_payload(
            key,
            payload,
            labels=labels,
            instance_url=instance_url,
        )
        if card:
            cards.append(card)

    return _dedupe_card_list(cards, limit=24)


def _record_card_from_payload(
    key: str,
    payload: Dict[str, Any],
    *,
    labels: Set[str],
    instance_url: str,
) -> Optional[Dict[str, str]]:
    key_lower = str(key or "").lower()
    payload_with_label = dict(payload)
    if labels and not payload_with_label.get("label"):
        payload_with_label["label"] = sorted(labels)[0]
    if "Incident" in labels or "incident" in key_lower:
        return _citation_from_row(payload_with_label, instance_url=instance_url)
    if "Problem" in labels or "problem" in key_lower:
        payload_with_label.setdefault("label", "Problem")
        return _problem_card_from_row(payload_with_label, instance_url=instance_url)
    if "ChangeRequest" in labels or "change" in key_lower:
        payload_with_label.setdefault("label", "ChangeRequest")
        return _change_card_from_row(payload_with_label, instance_url=instance_url)
    if "RequestItem" in labels or "ritm" in key_lower or "request_item" in key_lower:
        payload_with_label.setdefault("label", "RequestItem")
        return _request_item_card_from_row(payload_with_label, instance_url=instance_url)
    if "ServiceRequest" in labels or key_lower in {"request", "service_request"}:
        payload_with_label.setdefault("label", "ServiceRequest")
        return _request_card_from_row(payload_with_label, instance_url=instance_url)
    if "CatalogItem" in labels or "catalog" in key_lower or key_lower in {"item", "cat_item"}:
        payload_with_label.setdefault("label", "CatalogItem")
        return _catalog_item_card_from_row(payload_with_label, instance_url=instance_url)
    if "Appliance" in labels or "appliance" in key_lower or "ci" in key_lower:
        payload_with_label.setdefault("label", "Appliance")
        return _appliance_card_from_row(payload_with_label, instance_url=instance_url)
    return None


def _problem_card_from_row(row: Dict[str, Any], *, instance_url: str) -> Optional[Dict[str, str]]:
    labels = _labels_for(row)
    number = _prefixed_number(
        _first_value(row, ("problem_number", "prb_number", "number")),
        ("PRB",),
    )
    specific_sys_id = _string_value(_first_value(row, ("problem_sys_id", "problem_id")))
    has_hint = bool(number or specific_sys_id or "Problem" in labels)
    sys_id = specific_sys_id or (_string_value(row.get("sys_id")) if has_hint else "")
    short_description = _string_value(
        _first_value(row, ("problem_short_description", "short_description", "description", "title"))
    )
    if not has_hint or not (sys_id or number):
        return None
    return _record_card(
        record_type="problem",
        label="Problem",
        table="problem",
        instance_url=instance_url,
        sys_id=sys_id,
        number=number,
        short_description=short_description,
        state=_string_value(_first_value(row, ("problem_state", "state_display", "state"))),
        opened_at=_string_value(_first_value(row, ("problem_opened_at", "opened_at"))),
    )


def _change_card_from_row(row: Dict[str, Any], *, instance_url: str) -> Optional[Dict[str, str]]:
    labels = _labels_for(row)
    number = _prefixed_number(
        _first_value(row, ("change_number", "change_request_number", "number", "ticket_number")),
        ("CHG",),
    )
    specific_sys_id = _string_value(_first_value(row, ("change_sys_id", "change_request_sys_id", "ticket_sys_id")))
    has_hint = bool(number or specific_sys_id or "ChangeRequest" in labels)
    sys_id = specific_sys_id or (_string_value(row.get("sys_id")) if has_hint else "")
    short_description = _string_value(
        _first_value(row, ("change_short_description", "change_request_short_description", "short_description", "description"))
    )
    if not has_hint or not (sys_id or number):
        return None
    return _record_card(
        record_type="change_request",
        label="Change request",
        table="change_request",
        instance_url=instance_url,
        sys_id=sys_id,
        number=number,
        short_description=short_description,
        state=_string_value(_first_value(row, ("change_state", "state_display", "state"))),
        opened_at=_string_value(_first_value(row, ("change_opened_at", "opened_at"))),
    )


def _request_card_from_row(row: Dict[str, Any], *, instance_url: str) -> Optional[Dict[str, str]]:
    labels = _labels_for(row)
    number = _prefixed_number(
        _first_value(row, ("request_number", "service_request_number", "ticket_number", "number")),
        ("REQ",),
    )
    specific_sys_id = _string_value(_first_value(row, ("request_sys_id", "service_request_sys_id", "ticket_sys_id")))
    has_hint = bool(number or specific_sys_id or "ServiceRequest" in labels)
    sys_id = specific_sys_id or (_string_value(row.get("sys_id")) if has_hint else "")
    short_description = _string_value(
        _first_value(row, ("request_short_description", "service_request_short_description", "short_description", "description"))
    )
    if not has_hint or not (sys_id or number):
        return None
    return _record_card(
        record_type="service_request",
        label="Request",
        table="sc_request",
        instance_url=instance_url,
        sys_id=sys_id,
        number=number,
        short_description=short_description,
        state=_string_value(_first_value(row, ("request_state", "state_display", "state"))),
        opened_at=_string_value(_first_value(row, ("request_opened_at", "opened_at"))),
    )


def _request_item_card_from_row(row: Dict[str, Any], *, instance_url: str) -> Optional[Dict[str, str]]:
    labels = _labels_for(row)
    number = _prefixed_number(
        _first_value(row, ("request_item_number", "ritm_number", "number")),
        ("RITM",),
    )
    specific_sys_id = _string_value(_first_value(row, ("request_item_sys_id", "ritm_sys_id")))
    has_hint = bool(number or specific_sys_id or "RequestItem" in labels)
    sys_id = specific_sys_id or (_string_value(row.get("sys_id")) if has_hint else "")
    short_description = _string_value(
        _first_value(row, ("request_item_short_description", "ritm_short_description", "short_description", "description"))
    )
    if not has_hint or not (sys_id or number):
        return None
    return _record_card(
        record_type="request_item",
        label="Requested item",
        table="sc_req_item",
        instance_url=instance_url,
        sys_id=sys_id,
        number=number,
        short_description=short_description,
        state=_string_value(_first_value(row, ("request_item_state", "ritm_state", "state_display", "state"))),
        opened_at=_string_value(_first_value(row, ("request_item_opened_at", "ritm_opened_at", "opened_at"))),
    )


def _catalog_item_card_from_row(row: Dict[str, Any], *, instance_url: str) -> Optional[Dict[str, str]]:
    labels = _labels_for(row)
    specific_sys_id = _string_value(_first_value(row, ("catalog_item_sys_id", "cat_item_sys_id", "catalog_sys_id")))
    specific_name = _string_value(_first_value(row, ("catalog_item_name", "cat_item_name")))
    has_hint = bool(specific_sys_id or specific_name or "CatalogItem" in labels)
    sys_id = specific_sys_id or (_string_value(row.get("sys_id")) if has_hint else "")
    name = specific_name or (_string_value(_first_value(row, ("display_name", "name"))) if has_hint else "")
    short_description = _string_value(
        _first_value(row, ("catalog_item_short_description", "cat_item_short_description", "short_description", "description"))
    )
    if not has_hint or not (sys_id or name):
        return None
    return _record_card(
        record_type="catalog_item",
        label="Catalog item",
        table="sc_cat_item",
        instance_url=instance_url,
        sys_id=sys_id,
        name=name,
        short_description=short_description,
        state=_string_value(_first_value(row, ("active", "state_display", "state"))),
        query_field="name",
    )


def _appliance_card_from_row(row: Dict[str, Any], *, instance_url: str) -> Optional[Dict[str, str]]:
    labels = _labels_for(row)
    specific_sys_id = _string_value(
        _first_value(row, ("appliance_sys_id", "ci_sys_id", "cmdb_ci_sys_id", "configuration_item_sys_id"))
    )
    specific_name = _string_value(_first_value(row, ("appliance_name", "ci_name", "cmdb_ci_name", "configuration_item_name")))
    has_cmdb_table = bool(_string_value(_first_value(row, ("sys_class_name", "appliance_table", "ci_table", "table"))))
    has_hint = bool(specific_sys_id or specific_name or has_cmdb_table or "Appliance" in labels)
    sys_id = specific_sys_id or (_string_value(row.get("sys_id")) if has_hint else "")
    name = specific_name or (_string_value(row.get("name")) if has_hint else "")
    short_description = _string_value(
        _first_value(row, ("appliance_short_description", "ci_short_description", "short_description", "description"))
    )
    table = _string_value(_first_value(row, ("sys_class_name", "appliance_table", "ci_table", "table"))) or "cmdb_ci"
    if not table.startswith("cmdb_ci"):
        table = "cmdb_ci"
    if not has_hint or not (sys_id or name):
        return None
    return _record_card(
        record_type="appliance",
        label="Appliance",
        table=table,
        instance_url=instance_url,
        sys_id=sys_id,
        name=name,
        short_description=short_description,
        state=_string_value(_first_value(row, ("operational_status", "install_status", "state"))),
        query_field="name",
    )


def _record_card(
    *,
    record_type: str,
    label: str,
    table: str,
    instance_url: str,
    sys_id: str = "",
    number: str = "",
    name: str = "",
    short_description: str = "",
    state: str = "",
    opened_at: str = "",
    query_field: str = "name",
) -> Dict[str, str]:
    return {
        "type": _string_value(record_type),
        "label": _string_value(label),
        "table": _string_value(table),
        "number": _string_value(number),
        "name": _string_value(name),
        "sys_id": _string_value(sys_id),
        "short_description": _string_value(short_description),
        "state": _string_value(state),
        "opened_at": _string_value(opened_at),
        "url": build_servicenow_record_url(
            instance_url,
            table=table,
            sys_id=sys_id,
            number=number,
            name=name or short_description,
            query_field=query_field,
        ),
    }


def _citation_from_number(number: str, *, instance_url: str) -> Optional[Dict[str, str]]:
    normalized_number = _incident_number(number)
    if not normalized_number:
        return None
    return {
        "type": "incident",
        "table": "incident",
        "number": normalized_number,
        "sys_id": "",
        "short_description": "",
        "state": "",
        "opened_at": "",
        "url": build_servicenow_incident_url(instance_url, number=normalized_number),
    }


def _record_card_from_number(number: str, *, instance_url: str) -> Optional[Dict[str, str]]:
    normalized_number = _prefixed_number(number, tuple(_NUMBER_PREFIX_TABLES.keys()))
    if not normalized_number:
        return None
    prefix = re.match(r"[A-Z]+", normalized_number)
    if not prefix:
        return None
    record_type, table, label = _NUMBER_PREFIX_TABLES.get(prefix.group(0), ("record", "", "Record"))
    if not table:
        return None
    return _record_card(
        record_type=record_type,
        label=label,
        table=table,
        instance_url=instance_url,
        number=normalized_number,
    )


def _normalized_text(value: Any) -> str:
    text = _string_value(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _first_value(row: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", []):
            return value
    return None


def _nested_incident_payload(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for key, value in row.items():
        payload = _as_mapping(value)
        if not payload:
            continue
        key_lower = str(key or "").lower()
        labels = _labels_for(value)
        number = _incident_number(_first_value(payload, ("incident_number", "number", "ticket_number")))
        if number and ("incident" in key_lower or "Incident" in labels or number.startswith("INC")):
            return payload
    return None


def _incident_number(value: Any) -> str:
    return _prefixed_number(value, ("INC",))


def _prefixed_number(value: Any, prefixes: Tuple[str, ...]) -> str:
    text = _string_value(value)
    if not text:
        return ""
    prefix_pattern = "|".join(re.escape(prefix) for prefix in prefixes)
    match = re.search(rf"\b(?:{prefix_pattern})\d+\b", text, flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


def _as_mapping(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        return None
    items = getattr(value, "items", None)
    if not callable(items):
        return None
    try:
        return dict(items())
    except Exception:
        return None


def _labels_for(value: Any) -> Set[str]:
    labels = getattr(value, "labels", None)
    if labels:
        return {str(label) for label in labels}
    if isinstance(value, dict):
        raw_labels = value.get("labels") or value.get("_labels") or value.get("label")
        if isinstance(raw_labels, (list, tuple, set)):
            return {str(label) for label in raw_labels}
        if raw_labels:
            return {str(raw_labels)}
    return set()


def _string_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("display_value") or value.get("value")
    elif _as_mapping(value):
        return ""
    return str(value or "").strip()

import logging
from typing import Any, Dict, List, Optional

from app.models.catalog import (
    CatalogFormResponse,
    CatalogItem,
    CatalogVariable,
    CatalogVariableChoice,
)
from app.services import servicenow as snow
from app.services.neo4j_catalog_graph import platform_neo4j_catalog_graph_service as neo4j_catalog_graph_service

logger = logging.getLogger(__name__)


def sync_catalog_form_model_to_graph(
    form: CatalogFormResponse,
    sys_id: Optional[str] = None,
    enrich: bool = True,
) -> bool:
    normalized_sys_id = str(sys_id or form.item.sys_id or "").strip()
    if not neo4j_catalog_graph_service.is_available():
        return False

    working_form = form
    if enrich and normalized_sys_id:
        try:
            working_form = enrich_catalog_form(working_form, normalized_sys_id)
        except Exception:
            logger.exception("Failed to enrich catalog form %s before graph sync", normalized_sys_id)

    return neo4j_catalog_graph_service.sync_catalog_form(working_form)


def sync_catalog_item_to_graph(sys_id: str, enrich: bool = True) -> bool:
    form = build_catalog_form_from_servicenow(sys_id)
    if form is None:
        return False
    return sync_catalog_form_model_to_graph(form, sys_id=sys_id, enrich=enrich)


def build_catalog_form_from_servicenow(sys_id: str) -> Optional[CatalogFormResponse]:
    item = snow.get_catalog_item(sys_id)
    if not item:
        return None

    variables = [_normalize_catalog_variable(variable) for variable in snow.list_catalog_item_variables(sys_id)]
    return CatalogFormResponse(
        item=CatalogItem(
            sys_id=str(item.get("sys_id", "")),
            name=str(item.get("name") or ""),
            display_name=_as_optional_string(item.get("display_name") or item.get("name")),
            short_description=_as_optional_string(item.get("short_description")),
            description=_as_optional_string(item.get("description")),
            category=_extract_ref_display(item.get("category")) or _as_optional_string(item.get("category_name")) or _extract_ref_value(item.get("category")),
            category_sys_id=_as_optional_string(item.get("category_sys_id")) or _extract_ref_value(item.get("category")),
            category_name=_as_optional_string(item.get("category_name")) or _extract_ref_display(item.get("category")),
            icon=_as_optional_string(item.get("icon")),
            price=_as_optional_string(item.get("price")),
        ),
        variables=variables,
    )


def enrich_catalog_form(form: CatalogFormResponse, sys_id: str) -> CatalogFormResponse:
    ui_mappings_raw = snow.list_catalog_ui_policies(sys_id)
    raw_vars = snow.list_catalog_item_variables(sys_id)

    ui_mappings = ui_mappings_raw if isinstance(ui_mappings_raw, list) else []
    mapping_by_var = {mapping["variable_sys_id"]: mapping for mapping in ui_mappings if mapping.get("variable_sys_id")}

    native_deps: Dict[str, Dict[str, Optional[str]]] = {}
    if isinstance(raw_vars, list):
        for raw_var in raw_vars:
            var_id = _extract_ref_value(raw_var.get("sys_id"))
            dep_sys_id = _extract_ref_value(raw_var.get("depends_on")) if raw_var.get("depends_on") else None
            inactive_on_value = _extract_ref_value(raw_var.get("inactive_on_value")) if raw_var.get("inactive_on_value") else None
            if dep_sys_id and var_id:
                native_deps[var_id] = {
                    "depends_on": dep_sys_id,
                    "inactive_on_value": inactive_on_value,
                }

    enriched_variables: List[CatalogVariable] = []
    for variable in form.variables:
        updates: Dict[str, Any] = {}
        if variable.sys_id in mapping_by_var:
            mapping = mapping_by_var[variable.sys_id]
            updates["depends_on"] = mapping.get("depends_on")
            updates["active_on_value"] = mapping.get("active_on_value")
        elif variable.sys_id in native_deps and not variable.depends_on:
            native_dep = native_deps[variable.sys_id]
            updates["depends_on"] = native_dep.get("depends_on")
            if native_dep.get("inactive_on_value"):
                updates["inactive_on_value"] = native_dep.get("inactive_on_value")

        enriched_variables.append(variable.model_copy(update=updates) if updates else variable)

    return form.model_copy(update={"variables": enriched_variables})


def _normalize_catalog_variable(variable: Dict[str, Any]) -> CatalogVariable:
    variable_sys_id = str(_extract_ref_value(variable.get("sys_id")) or "")
    normalized_type = _map_variable_type(_extract_ref_value(variable.get("type")))

    choices: List[CatalogVariableChoice] = []
    if normalized_type in {"select", "radio"} and variable_sys_id:
        try:
            choices = [
                CatalogVariableChoice(
                    label=str(choice.get("text") or choice.get("value") or ""),
                    value=str(choice.get("value") or choice.get("text") or ""),
                )
                for choice in snow.list_variable_choices(variable_sys_id)
                if choice.get("text") or choice.get("value")
            ]
        except Exception:
            logger.exception("Failed to load choices for catalog variable %s", variable_sys_id)

    depends_on = variable.get("depends_on")
    depends_on_value = None
    if depends_on:
        if isinstance(depends_on, dict):
            depends_on_value = (
                depends_on.get("value")
                or depends_on.get("sys_id")
                or depends_on.get("display_value")
            )
        else:
            depends_on_value = str(depends_on)

    inactive_on_value = variable.get("inactive_on_value")
    active_on_value = variable.get("active_on_value")
    if depends_on_value and not active_on_value:
        active_on_value = variable.get("active_condition") or variable.get("question_choice")

    return CatalogVariable(
        sys_id=variable_sys_id,
        name=str(_extract_ref_value(variable.get("name")) or variable_sys_id),
        label=str(
            _as_optional_string(variable.get("question_text"))
            or _as_optional_string(variable.get("label"))
            or _extract_ref_value(variable.get("name"))
            or "Field"
        ),
        type=normalized_type,
        mandatory=_is_true(_extract_ref_value(variable.get("mandatory"))),
        default_value=_as_optional_string(variable.get("default_value")),
        choices=choices,
        depends_on=depends_on_value,
        inactive_on_value=_as_optional_string(inactive_on_value),
        active_on_value=_as_optional_string(active_on_value),
    )


def _extract_ref_value(field: Any) -> Optional[str]:
    if isinstance(field, dict):
        return field.get("value") or field.get("display_value") or None
    if field in (None, ""):
        return None
    return str(field).strip()


def _extract_ref_display(field: Any) -> Optional[str]:
    if isinstance(field, dict):
        return field.get("display_value") or field.get("value") or None
    if field in (None, ""):
        return None
    return str(field).strip()


def _map_variable_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip().lower()
    mapping = {
        "1": "text",
        "2": "textarea",
        "3": "select",
        "4": "checkbox",
        "5": "select",
        "6": "reference",
        "7": "checkbox",
        "8": "radio",
        "9": "date",
        "10": "datetime",
        "11": "label",
        "single line text": "text",
        "multi line text": "textarea",
        "multiple choice": "select",
        "select box": "select",
        "checkbox": "checkbox",
        "reference": "reference",
        "date": "date",
        "date/time": "datetime",
    }
    return mapping.get(value, "text")


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _as_optional_string(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        return str(value.get("display_value") or value.get("value") or "").strip() or None
    return str(value).strip() or None

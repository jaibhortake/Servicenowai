"""Mock ServiceNow API for testing when real ServiceNow is unreachable."""
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Mock UI policies for the "Report Outage" form (38c1fc840a0a0b2700285921c2bf5fc8)
MOCK_UI_POLICIES = {
    "38c1fc840a0a0b2700285921c2bf5fc8": [
        {
            "variable_sys_id": "38c651990a0a0b2700e2d3484bd76af6",  # outage_start
            "depends_on": "390b7b3a0a0a0b2700e901a602071db5",  # failure_mode
            "active_on_value": "outage",
        },
        {
            "variable_sys_id": "38c56db30a0a0b27000e9c6778a1dc64",  # cmdb_ci
            "depends_on": "390b7b3a0a0a0b2700e901a602071db5",  # failure_mode
            "active_on_value": "hardware",
        },
    ]
}


def get_mock_ui_policies(catalog_item_sys_id: str) -> List[Dict[str, Any]]:
    """Get mock UI policies for testing."""
    logger.info(f"[MOCK] Fetching mock UI policies for {catalog_item_sys_id}")
    mappings = MOCK_UI_POLICIES.get(catalog_item_sys_id, [])
    if mappings:
        logger.info(f"[MOCK] Returning {len(mappings)} mock UI policy mappings")
        for m in mappings:
            logger.info(f"[MOCK]   - var={m['variable_sys_id']} depends_on={m['depends_on']} active_on_value={m['active_on_value']}")
    else:
        logger.warning(f"[MOCK] No mock data for {catalog_item_sys_id}. Available keys: {list(MOCK_UI_POLICIES.keys())}")
    return mappings
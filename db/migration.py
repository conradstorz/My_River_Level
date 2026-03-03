from db.models import get_db, set_setting


def migrate_from_config(config_module, db_path=None):
    """
    Seed the database from a legacy config.py module.
    Safe to call multiple times — uses INSERT OR IGNORE for sites.
    """
    conn = get_db(db_path)

    # Migrate sites
    param = getattr(config_module, "PARAMETER_CODE", "00060")
    for site_number in getattr(config_module, "MONITORING_SITES", []):
        conn.execute(
            "INSERT OR IGNORE INTO sites (site_number, parameter_code) VALUES (?, ?)",
            (site_number, param)
        )

    conn.commit()
    conn.close()

    # Migrate scalar settings
    mapping = {
        "historical_start_year": "HISTORICAL_START_YEAR",
        "low_percentile": "LOW_FLOW_PERCENTILE",
        "high_percentile": "HIGH_FLOW_PERCENTILE",
        "very_low_percentile": "VERY_LOW_PERCENTILE",
        "very_high_percentile": "VERY_HIGH_PERCENTILE",
        "search_radius_miles": "SEARCH_RADIUS_MILES",
    }
    for db_key, config_attr in mapping.items():
        value = getattr(config_module, config_attr, None)
        if value is not None:
            set_setting(db_key, str(value), db_path)

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate virtual environment (Windows)
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run monitor with default config
python river_monitor.py

# Run with a named config
python river_monitor.py --config Bushmans

# List available configs
python river_monitor.py --list-configs

# Run interactive setup wizard
python setup_wizard.py
python setup_wizard.py --config <name>   # create/overwrite a named config
python setup_wizard.py --list            # list existing configs
```

There is no test suite in this project.

## Shell commands

Never chain or pipe bash commands. Run one command at a time. Do not use `&&`, `||`, `|`, or `;` to combine commands in a single Bash call.

## Service Commands (run as Administrator)

```bash
# Install and manage the Windows service
python service.py install
python service.py start
python service.py stop
python service.py remove

# Run without service manager (development/debug)
python service.py debug
```

The service runs on `http://localhost:5743`. Logs go to `logs/river_monitor.log`.
On first start, if `config.py` exists its sites and settings are automatically migrated to the SQLite database at `db/river_monitor.db`.

## Architecture

The project monitors USGS stream gauges for extreme water conditions (flood/drought) by comparing real-time readings against percentile thresholds derived from historical data.

### Core flow (`river_monitor.py`)

1. `main()` parses args, loads a config module dynamically via `importlib.util`, and detects first-run (no sites configured).
2. `RiverMonitor` is instantiated with a list of USGS site numbers and the config module.
3. For each site, `check_site_conditions()` fetches:
   - **Current data** via `nwis.get_iv()` — interval values for the past 7 days
   - **Historical data** via `nwis.get_dv()` — daily values from `HISTORICAL_START_YEAR` to today
4. `calculate_percentiles()` manually computes where the current value falls in the historical distribution using numpy (not hyswap).
5. `classify_condition()` maps percentile to severity using thresholds from config.
6. `print_report()` renders the formatted report; nearby sites are only shown when extreme conditions are present.

### Configuration system

Config files are plain Python modules (e.g., `config.py`, `Bushmans.py`) loaded dynamically. Multiple named configs can coexist. Each exposes:

| Variable | Purpose |
|---|---|
| `MONITORING_SITES` | List of USGS 8-digit site number strings |
| `LOCATION` | `{"latitude": float, "longitude": float}` for bounding-box site discovery |
| `SEARCH_RADIUS_MILES` | Radius used when discovering sites dynamically |
| `PARAMETER_CODE` | `"00060"` (discharge, cfs) or `"00065"` (gage height, ft) |
| `LOW/HIGH/VERY_LOW/VERY_HIGH_FLOW_PERCENTILE` | Alert thresholds |
| `HISTORICAL_START_YEAR` | Oldest year to pull for baseline statistics |

If `MONITORING_SITES` is empty but `LOCATION` has coordinates, the monitor auto-selects the top 5 nearby sites.

### Setup wizard (`setup_wizard.py`)

Standalone interactive tool that:
1. Geocodes an address via Nominatim (OpenStreetMap REST API)
2. Finds active USGS gauges in a bounding box via `nwis.what_sites()`
3. Shows a numbered list with recent data previews for selection
4. Writes a new `<name>.py` config file

### USGS API (`dataretrieval`)

| Call | Purpose |
|---|---|
| `nwis.what_sites(bBox=..., parameterCd=..., siteStatus="active")` | Discover gauges in bounding box |
| `nwis.get_iv(sites=..., parameterCd=..., start=..., end=...)` | Real-time/interval values |
| `nwis.get_dv(sites=..., parameterCd=..., start=..., end=...)` | Historical daily values |
| `nwis.get_info(sites=...)` | Site metadata (station name, etc.) |

Column names returned by `get_iv` use the pattern `"00060"` or `"00060_00000"`. Daily values (`get_dv`) use `"00060_Mean"`. The code filters columns by `startswith(param_code)` or `param_code in col`.

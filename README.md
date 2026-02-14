# River Level Extreme Conditions Monitor

A Python system that monitors USGS stream gauges to detect and report extreme water conditions (floods and droughts) for waterways.

## Features

- 🔍 Find USGS monitoring gauges near any location
- 📊 Fetch real-time and historical water data
- 📈 Calculate statistical percentiles to identify extreme conditions
- ⚠️ Alert on flood and drought conditions
- 📋 Generate detailed condition reports

## Based on USGS Data Tools

This project uses the modernized USGS Water Data for the Nation services:
- **dataretrieval**: Access USGS water data
- **hyswap**: Statistical analysis for surface water
- Data from: https://waterdata.usgs.gov/

Reference: https://waterdata.usgs.gov/blog/wdfn-stats-delivery/

## Installation

### Prerequisites
- Python 3.8 or higher
- Git (for cloning the repository)

### Setup with Virtual Environment (Recommended)

**Option 1: Using venv (built-in)**
```bash
# Clone the repository
git clone git@github.com:conradstorz/My_River_Level.git
cd My_River_Level

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the setup wizard
python setup_wizard.py
```

**Option 2: Using uv (faster alternative)**
```bash
# Install uv if you don't have it
pip install uv

# Clone the repository
git clone git@github.com:conradstorz/My_River_Level.git
cd My_River_Level

# Create virtual environment with uv
uv venv

# Activate virtual environment
# On Windows:
.venv\Scripts\activate
# On Linux/Mac:
source .venv/bin/activate

# Install dependencies with uv (much faster)
uv pip install -r requirements.txt

# Run the setup wizard
python setup_wizard.py
```

### Quick Setup (Automated)

**Windows:**
```bash
setup.bat
```

**Linux/Mac:**
```bash
chmod +x setup.sh
./setup.sh
```

These scripts will automatically create a virtual environment and install all dependencies.

### Verifying Installation
```bash
python -c "import dataretrieval; print('✓ Dependencies installed successfully')"
```

### Deactivating Virtual Environment
When you're done, deactivate the virtual environment:
```bash
deactivate
```
   
The wizard will:
- Help you find your location (by address or coordinates)
- Search for nearby USGS stream gauges
- Let you select which gauges to monitor
- Automatically configure the system

## Alternative: Manual Configuration

Edit `config.py` to set up your monitoring:

### Option 1: Monitor specific gauges
```python
MONITORING_SITES = [
    "01646500",  # Potomac River near Washington, DC
    "01638500",  # Potomac River at Point of Rocks, MD
]
```

### Option 2: Find gauges near a location
```python
LOCATION = {
    "latitude": 38.9072,   # Washington, DC
    "longitude": -77.0369,
}
SEARCH_RADIUS_MILES = 25
```

Find gauge numbers at: https://waterdata.usgs.gov/

### Adjust alert thresholds
```python
LOW_FLOW_PERCENTILE = 10   # Bottom 10% = drought
HIGH_FLOW_PERCENTILE = 90  # Top 90% = flood risk
VERY_LOW_PERCENTILE = 5    # Severe drought
VERY_HIGH_PERCENTILE = 95  # Severe flood
```

## Usage

### First Run

On first run, the monitor will automatically prompt you to run the setup wizard:

```bash
python river_monitor.py
```

If you haven't configured any gauges, you'll see:
```
⚠️  FIRST RUN DETECTED
Would you like to run the setup wizard? (y/n)
```

### Running the Setup Wizard Manually

You can re-run the setup wizard anytime:

```bash
python setup_wizard.py
```

The wizard provides an interactive experience:
1. **Location Entry**: Enter an address or coordinates
2. **Gauge Search**: Finds active USGS gauges nearby
3. **Gauge Selection**: Preview and select gauges to monitor
4. **Auto-Configuration**: Saves settings to config.py

### Running the Monitor

After configuration, simply run:
```bash
python river_monitor.py
```

### Sample Output
```
================================================================================
RIVER LEVEL EXTREME CONDITIONS REPORT
Generated: 2026-02-14 10:30:00
================================================================================

⚠️ ALERT Site: 01646500
  Current Value: 2458.00 cfs
  As of: 2026-02-14 10:15:00
  Condition: SEVERE LOW (Severe drought conditions)
  Percentile: 3.2%
  Historical Range: 1200.00 - 425000.00 cfs
  Historical Median: 8950.00 cfs

✓ Site: 01638500
  Current Value: 8523.00 cfs
  As of: 2026-02-14 10:15:00
  Condition: NORMAL (Normal flow conditions)
  Percentile: 48.5%
  Historical Range: 850.00 - 335000.00 cfs
  Historical Median: 8200.00 cfs

================================================================================
Summary: 1 of 2 sites show extreme conditions
================================================================================
```

## Understanding the Data

### Parameter Codes
- **00060**: Discharge (streamflow) in cubic feet per second (cfs)
- **00065**: Gage height in feet

### Condition Classifications
- **SEVERE HIGH** (≥95th percentile): Severe flood conditions
- **HIGH** (≥90th percentile): Above normal flow, flood risk
- **NORMAL** (10th-90th percentile): Normal conditions
- **LOW** (≤10th percentile): Below normal flow, drought
- **SEVERE LOW** (≤5th percentile): Severe drought conditions

## Project Structure

```
My_River_level/
├── river_monitor.py     # Main monitoring script
├── setup_wizard.py      # Interactive setup tool
├── config.py            # Configuration settings
├── requirements.txt     # Python dependencies
└── README.md           # This file
```

## Next Steps

- Add email/SMS alerts for extreme conditions
- Create visualizations with matplotlib
- Build a web dashboard
- Schedule automated monitoring
- Export reports to CSV/JSON
- Add support for multiple parameter types (stage, temperature, etc.)

## Resources

- [USGS Water Data for the Nation](https://waterdata.usgs.gov/)
- [dataretrieval Documentation](https://github.com/DOI-USGS/dataRetrieval)
- [hyswap Documentation](https://doi-usgs.github.io/hyswap/)
- [Find Monitoring Locations](https://waterdata.usgs.gov/nwis/rt)
- [National Water Dashboard](https://dashboard.waterdata.usgs.gov/)

## Support

For USGS data questions: gs-w_waterdata_support@usgs.gov

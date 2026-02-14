"""
Configuration for River Level Extreme Conditions Monitor
"""

# Default monitoring locations (USGS site numbers)
# You can find site numbers at: https://waterdata.usgs.gov/
MONITORING_SITES = [
    # Example: "01646500",  # Potomac River near Washington, DC
]

# Coordinates for finding nearby gauges (latitude, longitude)
LOCATION = {
    "latitude": None,  # Set your location
    "longitude": None,
}

# Search radius for nearby gauges (in miles)
SEARCH_RADIUS_MILES = 25

# Extreme condition thresholds (percentiles)
# Values below LOW_FLOW_PERCENTILE are considered drought/low flow
# Values above HIGH_FLOW_PERCENTILE are considered flood/high flow
LOW_FLOW_PERCENTILE = 10  # Bottom 10%
HIGH_FLOW_PERCENTILE = 90  # Top 10%

# Alert thresholds for very extreme conditions
VERY_LOW_PERCENTILE = 5   # Severe drought
VERY_HIGH_PERCENTILE = 95  # Severe flood

# Parameter codes
# 00060 = Discharge (streamflow) in cubic feet per second
# 00065 = Gage height in feet
PARAMETER_CODE = "00060"  # Discharge

# Historical data range for calculating statistics
HISTORICAL_START_YEAR = 1980

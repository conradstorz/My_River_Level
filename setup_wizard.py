"""
Interactive Setup Wizard for River Level Monitor
Helps users configure their monitoring location and select gauges
"""

import dataretrieval.nwis as nwis
import requests
from datetime import datetime, timedelta
import sys
import argparse
import os


def get_coordinates_from_address(address):
    """
    Convert an address to coordinates using Nominatim (OpenStreetMap)
    
    Args:
        address: Street address or city/state
        
    Returns:
        Tuple of (latitude, longitude) or None
    """
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': address,
            'format': 'json',
            'limit': 1
        }
        headers = {
            'User-Agent': 'River-Level-Monitor/1.0'
        }
        
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        results = response.json()
        if results:
            lat = float(results[0]['lat'])
            lon = float(results[0]['lon'])
            display_name = results[0]['display_name']
            print(f"\n✓ Found: {display_name}")
            return lat, lon
        else:
            print("\n✗ Location not found")
            return None
            
    except Exception as e:
        print(f"\n✗ Error geocoding address: {e}")
        return None


def find_nearby_gauges(latitude, longitude, radius_miles=50):
    """
    Find USGS monitoring sites near a location
    
    Args:
        latitude: Latitude
        longitude: Longitude
        radius_miles: Search radius
        
    Returns:
        DataFrame of nearby sites
    """
    try:
        print(f"\n🔍 Searching for gauges within {radius_miles} miles...")
        
        # Convert miles to decimal degrees (approximate)
        radius_dd = radius_miles / 69.0
        
        # Find sites with discharge data
        sites = nwis.what_sites(
            bBox=f"{longitude - radius_dd},{latitude - radius_dd},"
                  f"{longitude + radius_dd},{latitude + radius_dd}",
            parameterCd="00060",  # Discharge
            siteStatus="active",
            hasDataTypeCd="dv"  # Daily values
        )
        
        if len(sites) == 0:
            print("✗ No active gauges found in this area")
            return None
            
        print(f"✓ Found {len(sites)} active stream gauges")
        return sites
        
    except Exception as e:
        print(f"✗ Error finding gauges: {e}")
        return None


def get_gauge_preview(site_number):
    """
    Get a preview of recent data from a gauge
    
    Args:
        site_number: USGS site number
        
    Returns:
        String with preview info
    """
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        df, _ = nwis.get_dv(
            sites=site_number,
            parameterCd="00060",
            start=start_date.strftime('%Y-%m-%d'),
            end=end_date.strftime('%Y-%m-%d')
        )
        
        if len(df) > 0:
            recent_value = df.iloc[-1, 0]
            recent_date = df.index[-1].strftime('%Y-%m-%d')
            return f"Recent: {recent_value:.0f} cfs ({recent_date})"
        else:
            return "No recent data"
            
    except Exception as e:
        return "Data unavailable"


def select_gauges(sites_df):
    """
    Interactive gauge selection
    
    Args:
        sites_df: DataFrame of available sites
        
    Returns:
        List of selected site numbers
    """
    print("\n" + "="*80)
    print("AVAILABLE STREAM GAUGES")
    print("="*80)
    
    # Show gauges with details
    for idx, row in sites_df.iterrows():
        site_no = row['site_no']
        station_name = row['station_nm']
        
        print(f"\n[{idx + 1}] {site_no}")
        print(f"    Name: {station_name}")
        
        # Get preview data
        preview = get_gauge_preview(site_no)
        print(f"    {preview}")
    
    print("\n" + "="*80)
    print("\nEnter the numbers of gauges to monitor (comma-separated)")
    print("Examples: '1,3,5' or '1-3' or 'all' or 'q' to quit")
    print("="*80)
    
    selected_sites = []
    
    while True:
        user_input = input("\nYour selection: ").strip().lower()
        
        if user_input == 'q':
            return None
        
        if user_input == 'all':
            selected_sites = sites_df['site_no'].tolist()
            break
        
        try:
            # Parse selection
            selected_indices = []
            
            for part in user_input.split(','):
                part = part.strip()
                
                if '-' in part:
                    # Range selection
                    start, end = part.split('-')
                    selected_indices.extend(range(int(start), int(end) + 1))
                else:
                    # Single selection
                    selected_indices.append(int(part))
            
            # Convert to site numbers
            for idx in selected_indices:
                if 1 <= idx <= len(sites_df):
                    site_no = sites_df.iloc[idx - 1]['site_no']
                    if site_no not in selected_sites:
                        selected_sites.append(site_no)
                else:
                    print(f"✗ Invalid selection: {idx}")
            
            if selected_sites:
                break
            else:
                print("✗ No valid selections made")
                
        except Exception as e:
            print(f"✗ Invalid input: {e}")
            print("Please try again")
    
    return selected_sites


def save_configuration(site_numbers, latitude=None, longitude=None, radius=25, config_name='config'):
    """
    Save configuration to a config file
    
    Args:
        site_numbers: List of USGS site numbers
        latitude: Optional latitude
        longitude: Optional longitude
        radius: Search radius
        config_name: Name of config file (without .py)
    """
    config_file = f"{config_name}.py"
    
    config_content = f'''"""
Configuration for River Level Extreme Conditions Monitor
Generated by setup wizard on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Config name: {config_name}
"""

# Monitoring locations (USGS site numbers)
MONITORING_SITES = {site_numbers}

# Location coordinates (for finding nearby gauges)
LOCATION = {{
    "latitude": {latitude if latitude else "None"},
    "longitude": {longitude if longitude else "None"},
}}

# Search radius for nearby gauges (in miles)
SEARCH_RADIUS_MILES = {radius}

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
'''
    
    with open(config_file, 'w') as f:
        f.write(config_content)
    
    print(f"\n✓ Configuration saved to {config_file}")


def run_wizard(config_name='config'):
    """
    Run the interactive setup wizard
    
    Args:
        config_name: Name for the configuration file (without .py)
    """
    
    print("="*80)
    print("RIVER LEVEL MONITOR - SETUP WIZARD")
    print("="*80)
    print("\nThis wizard will help you configure your water monitoring system.")
    print("You can monitor stream gauges near any location in the United States.")
    print(f"\nConfiguration will be saved as: {config_name}.py")
    
    # Get location
    print("\n" + "-"*80)
    print("STEP 1: LOCATION")
    print("-"*80)
    print("\nHow would you like to specify your location?")
    print("  [1] Enter an address or city")
    print("  [2] Enter latitude/longitude coordinates")
    print("  [3] Skip (manually configure later)")
    
    location_method = input("\nYour choice (1-3): ").strip()
    
    latitude = None
    longitude = None
    
    if location_method == '1':
        # Address input
        print("\nExamples:")
        print("  - Louisville, KY")
        print("  - 123 Main St, Cincinnati, OH")
        print("  - Ohio River")
        
        address = input("\nEnter address or location: ").strip()
        
        if address:
            coords = get_coordinates_from_address(address)
            if coords:
                latitude, longitude = coords
    
    elif location_method == '2':
        # Coordinate input
        try:
            latitude = float(input("\nEnter latitude: ").strip())
            longitude = float(input("Enter longitude: ").strip())
            print(f"\n✓ Location set to: {latitude}, {longitude}")
        except ValueError:
            print("\n✗ Invalid coordinates")
    
    # Find gauges if location provided
    selected_sites = []
    
    if latitude and longitude:
        print("\n" + "-"*80)
        print("STEP 2: SEARCH RADIUS")
        print("-"*80)
        
        radius_input = input("\nSearch radius in miles (default: 50): ").strip()
        radius = int(radius_input) if radius_input else 50
        
        sites_df = find_nearby_gauges(latitude, longitude, radius)
        
        if sites_df is not None and len(sites_df) > 0:
            print("\n" + "-"*80)
            print("STEP 3: SELECT GAUGES")
            print("-"*80)
            
            selected_sites = select_gauges(sites_df)
            
            if selected_sites is None:
                print("\n✗ Setup cancelled")
                return False
    else:
        print("\n⚠️  No location specified. You'll need to manually add gauge numbers to config.py")
        print("Find gauges at: https://waterdata.usgs.gov/")
    
    # Save configuration
    if selected_sites:
        print("\n" + "-"*80)
        print("STEP 4: SAVE CONFIGURATION")
        print("-"*80)
        print(f"\n✓ Selected {len(selected_sites)} gauge(s):")
        for site_no in selected_sites:
            print(f"  - {site_no}")
        
        confirm = input("\nSave this configuration? (y/n): ").strip().lower()
        
        if confirm == 'y':
            save_configuration(selected_sites, latitude, longitude, radius, config_name)
            
            print("\n" + "="*80)
            print("✓ SETUP COMPLETE!")
            print("="*80)
            print("\nYou can now run the monitor with:")
            if config_name == 'config':
                print("  python river_monitor.py")
            else:
                print(f"  python river_monitor.py --config {config_name}")
            print("\nTo reconfigure, run:")
            if config_name == 'config':
                print("  python setup_wizard.py")
            else:
                print(f"  python setup_wizard.py --config {config_name}")
            print("="*80 + "\n")
            
            return True
        else:
            print("\n✗ Configuration not saved")
            return False
    else:
        # Save empty config
        save_configuration([], latitude, longitude, 25, config_name)
        
        print("\n" + "="*80)
        print("⚠️  SETUP INCOMPLETE")
        print("="*80)
        print("\nNo gauges were selected. Please:")
        print(f"1. Edit {config_name}.py to add MONITORING_SITES")
        print("2. Or run setup_wizard.py again")
        print("="*80 + "\n")
        
        return False


if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='River Level Monitor Setup Wizard')
    parser.add_argument('--config', '-c', default='config',
                        help='Configuration name (without .py extension). Default: config')
    parser.add_argument('--list', '-l', action='store_true',
                        help='List existing configuration files')
    
    args = parser.parse_args()
    
    # List configurations if requested
    if args.list:
        print("\n" + "="*80)
        print("EXISTING CONFIGURATIONS")
        print("="*80)
        
        config_files = [f for f in os.listdir('.') if f.startswith('config') and f.endswith('.py')]
        
        if config_files:
            for config_file in sorted(config_files):
                config_name = config_file[:-3]
                print(f"  - {config_name}")
            print("\nTo create/edit a config: python setup_wizard.py --config <name>")
        else:
            print("  No configuration files found")
        
        print("="*80 + "\n")
        sys.exit(0)
    
    # Check if config exists
    config_file = f"{args.config}.py"
    if os.path.exists(config_file):
        print(f"\n⚠️  Configuration '{config_file}' already exists!")
        response = input("Overwrite it? (y/n): ").strip().lower()
        if response != 'y':
            print("✗ Setup cancelled")
            sys.exit(0)
    
    try:
        run_wizard(config_name=args.config)
    except KeyboardInterrupt:
        print("\n\n✗ Setup cancelled by user")
        sys.exit(1)

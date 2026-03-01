"""
River Level Extreme Conditions Monitor
Uses USGS gauge data to detect extreme water conditions
"""

import dataretrieval.nwis as nwis
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import sys
import argparse
import importlib.util


class RiverMonitor:
    """Monitor river conditions using USGS gauge data"""
    
    def __init__(self, site_numbers=None, config_module=None):
        """
        Initialize the river monitor
        
        Args:
            site_numbers: List of USGS site numbers to monitor
            config_module: Configuration module with settings
        """
        self.site_numbers = site_numbers if site_numbers else []
        self.config = config_module
        self.site_info_cache = {}  # Cache for site information
        
    def find_nearby_sites(self, latitude, longitude, radius_miles=25):
        """
        Find USGS monitoring sites near a location
        
        Args:
            latitude: Latitude of location
            longitude: Longitude of location
            radius_miles: Search radius in miles
            
        Returns:
            DataFrame of nearby sites
        """
        try:
            # Convert miles to decimal degrees (approximate)
            # 1 degree latitude = ~69 miles
            # 1 degree longitude varies by latitude, using cosine approximation
            import math
            lat_offset = radius_miles / 69.0
            lon_offset = radius_miles / (69.0 * math.cos(math.radians(latitude)))
            
            west = longitude - lon_offset
            south = latitude - lat_offset
            east = longitude + lon_offset
            north = latitude + lat_offset
            
            # Format bounding box string (USGS format: west,south,east,north)
            bbox_str = f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"
            
            # Find sites within bounding box
            sites, _ = nwis.what_sites(
                bBox=bbox_str,
                parameterCd=self.config.PARAMETER_CODE if self.config else "00060",
                siteStatus="active"
            )
            
            return sites
            
        except Exception as e:
            print(f"Error finding nearby sites: {e}")
            return None
    
    def get_current_data(self, site_number):
        """
        Get current/recent data for a site
        
        Args:
            site_number: USGS site number
            
        Returns:
            DataFrame with current data
        """
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            
            param_code = self.config.PARAMETER_CODE if self.config else "00060"
            
            df, _ = nwis.get_iv(
                sites=site_number,
                parameterCd=param_code,
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d')
            )
            
            # Check if we got valid data
            if df is None or len(df) == 0:
                return None
            
            # Find the column with the parameter data
            # Columns are typically named like '00060' or '00060_00000'
            param_cols = [col for col in df.columns if col.startswith(param_code)]
            
            if not param_cols:
                print(f"  Warning: No column found for parameter {param_code}")
                return None
            
            # Return only the parameter column(s)
            return df[param_cols]
            
        except Exception as e:
            print(f"Error getting current data for site {site_number}: {e}")
            return None
    
    def get_historical_data(self, site_number, start_year=None):
        """
        Get historical daily values for statistical comparison
        
        Args:
            site_number: USGS site number
            start_year: Starting year for historical data
            
        Returns:
            DataFrame with historical data
        """
        try:
            start_year = start_year or (self.config.HISTORICAL_START_YEAR if self.config else 1980)
            start_date = f"{start_year}-01-01"
            end_date = datetime.now().strftime('%Y-%m-%d')
            
            param_code = self.config.PARAMETER_CODE if self.config else "00060"
            
            df, _ = nwis.get_dv(
                sites=site_number,
                parameterCd=param_code,
                start=start_date,
                end=end_date
            )
            
            # Check if we got valid data
            if df is None or len(df) == 0:
                return None
            
            # Find the column with the parameter data
            # For daily values, columns are typically named like '00060_Mean'
            param_cols = [col for col in df.columns if param_code in col]
            
            if not param_cols:
                print(f"  Warning: No column found for parameter {param_code}")
                return None
            
            # Return only the parameter column(s)
            return df[param_cols]
            
        except Exception as e:
            print(f"Error getting historical data for site {site_number}: {e}")
            return None
    
    def get_site_info(self, site_number):
        """
        Get site information including station name
        
        Args:
            site_number: USGS site number
            
        Returns:
            Dictionary with site information
        """
        # Check cache first
        if site_number in self.site_info_cache:
            return self.site_info_cache[site_number]
        
        try:
            # Get site information
            site_info, _ = nwis.get_info(sites=site_number)
            
            if site_info is not None and len(site_info) > 0:
                info = {
                    'site_number': site_number,
                    'station_name': site_info.iloc[0]['station_nm'] if 'station_nm' in site_info.columns else f"Site {site_number}"
                }
                self.site_info_cache[site_number] = info
                return info
            else:
                # Fallback if no info available
                info = {'site_number': site_number, 'station_name': f"Site {site_number}"}
                self.site_info_cache[site_number] = info
                return info
                
        except Exception as e:
            print(f"  Warning: Could not get site info: {e}")
            info = {'site_number': site_number, 'station_name': f"Site {site_number}"}
            self.site_info_cache[site_number] = info
            return info
    
    def get_parameter_unit(self):
        """
        Get the unit label for the configured parameter
        
        Returns:
            String unit label (e.g., 'cfs' or 'ft')
        """
        param_code = self.config.PARAMETER_CODE if self.config else "00060"
        
        # Common USGS parameter codes and their units
        units = {
            "00060": "cfs",  # Discharge (cubic feet per second)
            "00065": "ft",   # Gage height (feet)
            "00010": "°C",   # Temperature (Celsius)
            "00045": "in",   # Precipitation (inches)
        }
        
        return units.get(param_code, "units")
    
    def calculate_percentiles(self, historical_df, current_value):
        """
        Calculate what percentile the current value represents
        
        Args:
            historical_df: DataFrame with historical data
            current_value: Current discharge/stage value
            
        Returns:
            Percentile (0-100)
        """
        if historical_df is None or len(historical_df) == 0:
            return None
            
        # Get all historical values and convert to numeric
        values = pd.to_numeric(historical_df.iloc[:, 0], errors='coerce').values
        # Filter out NaN values and USGS error codes (negative values)
        values = values[~np.isnan(values) & (values >= 0)]
        
        if len(values) == 0:
            return None
        
        # Calculate percentile
        percentile = (values < current_value).sum() / len(values) * 100
        return percentile
    
    def classify_condition(self, percentile):
        """
        Classify water condition based on percentile
        
        Args:
            percentile: Percentile value (0-100)
            
        Returns:
            Tuple of (severity, description)
        """
        if percentile is None:
            return "UNKNOWN", "Insufficient data"
        
        # Use config thresholds if available, otherwise use defaults
        very_low = self.config.VERY_LOW_PERCENTILE if self.config else 5
        low = self.config.LOW_FLOW_PERCENTILE if self.config else 10
        high = self.config.HIGH_FLOW_PERCENTILE if self.config else 90
        very_high = self.config.VERY_HIGH_PERCENTILE if self.config else 95
        
        if percentile <= very_low:
            return "SEVERE LOW", "Severe drought - critically low level"
        elif percentile <= low:
            return "LOW", "Below normal level (drought)"
        elif percentile >= very_high:
            return "SEVERE HIGH", "Severe flood - critically high level"
        elif percentile >= high:
            return "HIGH", "Above normal level (flood risk)"
        else:
            return "NORMAL", "Normal level"
    
    def check_site_conditions(self, site_number):
        """
        Check current conditions at a specific site
        
        Args:
            site_number: USGS site number
            
        Returns:
            Dictionary with condition information
        """
        print(f"\nChecking site {site_number}...")
        
        # Get site information
        site_info = self.get_site_info(site_number)
        
        # Get current data
        current_df = self.get_current_data(site_number)
        if current_df is None or len(current_df) == 0:
            return None
        
        # Get most recent value from the first (parameter) column
        # The dataframe now contains only parameter columns
        current_value = pd.to_numeric(current_df.iloc[-1, 0], errors='coerce')
        current_time = current_df.index[-1]
        
        # Check if current value is valid
        # USGS uses -999999 or similar negative values as error codes
        if pd.isna(current_value) or current_value < 0:
            print(f"  No valid current data available (value: {current_value})")
            return None
        
        # Get historical data
        historical_df = self.get_historical_data(site_number)
        if historical_df is None:
            return None
        
        # Calculate percentile
        percentile = self.calculate_percentiles(historical_df, current_value)
        severity, description = self.classify_condition(percentile)
        
        # Get statistics from the first (parameter) column
        # The dataframe now contains only parameter columns
        values = pd.to_numeric(historical_df.iloc[:, 0], errors='coerce').values
        # Filter out NaN values and USGS error codes (negative values)
        values = values[~np.isnan(values) & (values >= 0)]
        
        # Check if we have valid historical data
        if len(values) == 0:
            print(f"  No valid historical data available")
            return None
        
        # Get parameter unit
        unit = self.get_parameter_unit()
        
        result = {
            'site_number': site_number,
            'station_name': site_info['station_name'],
            'current_value': current_value,
            'current_time': current_time,
            'percentile': percentile,
            'severity': severity,
            'description': description,
            'historical_min': np.min(values),
            'historical_max': np.max(values),
            'historical_median': np.median(values),
            'unit': unit,
        }
        
        return result
    
    def monitor_all_sites(self):
        """
        Check conditions at all configured sites
        
        Returns:
            List of condition dictionaries
        """
        results = []
        
        for site_number in self.site_numbers:
            result = self.check_site_conditions(site_number)
            if result:
                results.append(result)
        
        return results
    
    def print_report(self, results, nearby_sites=None):
        """
        Print a formatted report of conditions
        
        Args:
            results: List of condition dictionaries
            nearby_sites: Optional DataFrame of nearby sites (only shown if extreme conditions exist)
        """
        # Check if any extreme conditions exist
        extreme_count = sum(1 for r in results if r['severity'] in ['SEVERE LOW', 'SEVERE HIGH', 'LOW', 'HIGH'])
        
        # Only show nearby sites if there are extreme conditions
        if extreme_count > 0 and nearby_sites is not None:
            print("\nNearby Sites:")
            print(nearby_sites[['site_no', 'station_nm']].to_string())
        
        print("\n" + "="*80)
        print("RIVER LEVEL EXTREME CONDITIONS REPORT")
        print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        for result in results:
            is_extreme = result['severity'] in ['SEVERE LOW', 'SEVERE HIGH', 'LOW', 'HIGH']
            
            if is_extreme:
                marker = "⚠️ ALERT" if 'SEVERE' in result['severity'] else "⚡ WARNING"
            else:
                marker = "✓"
            
            print(f"\n{marker} {result['station_name']}")
            print(f"  Site Number: {result['site_number']}")
            print(f"  Current Level: {result['current_value']:.2f} {result['unit']}")
            print(f"  As of: {result['current_time']}")
            print(f"  Condition: {result['severity']} ({result['description']})")
            print(f"  Percentile: {result['percentile']:.1f}%")
            print(f"  Historical Range: {result['historical_min']:.2f} - "
                  f"{result['historical_max']:.2f} {result['unit']}")
            print(f"  Historical Median: {result['historical_median']:.2f} {result['unit']}")
        
        print("\n" + "="*80)
        print(f"Summary: {extreme_count} of {len(results)} sites show extreme conditions")
        print("="*80 + "\n")


def load_config(config_name='config'):
    """
    Load a configuration file
    
    Args:
        config_name: Name of config file (without .py extension)
        
    Returns:
        Loaded config module
    """
    config_file = f"{config_name}.py"
    
    if not os.path.exists(config_file):
        print(f"\n✗ Configuration file '{config_file}' not found")
        return None
    
    # Load the config module dynamically
    spec = importlib.util.spec_from_file_location(config_name, config_file)
    config_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    
    return config_module


def check_first_run(config_module):
    """
    Check if this is the first run and configuration is needed
    
    Args:
        config_module: Configuration module
        
    Returns:
        True if setup is needed, False otherwise
    """
    # Check if config has monitoring sites or location
    if not config_module.MONITORING_SITES and not (config_module.LOCATION['latitude'] and config_module.LOCATION['longitude']):
        return True
    return False


def main():
    """Main execution function"""
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='River Level Extreme Conditions Monitor')
    parser.add_argument('--config', '-c', default='config',
                        help='Configuration file to use (without .py extension). Default: config')
    parser.add_argument('--list-configs', '-l', action='store_true',
                        help='List available configuration files')
    
    args = parser.parse_args()
    
    # List configurations if requested
    if args.list_configs:
        print("\n" + "="*80)
        print("AVAILABLE CONFIGURATIONS")
        print("="*80)
        
        config_files = [f for f in os.listdir('.') if f.startswith('config') and f.endswith('.py')]
        
        if config_files:
            for config_file in sorted(config_files):
                config_name = config_file[:-3]  # Remove .py
                print(f"  - {config_name}")
            print("\nUse: python river_monitor.py --config <name>")
        else:
            print("  No configuration files found")
        
        print("="*80 + "\n")
        return
    
    # Load configuration
    print(f"\n📋 Loading configuration: {args.config}.py")
    config = load_config(args.config)
    
    if config is None:
        print("\nAvailable options:")
        print("  1. Run setup wizard: python setup_wizard.py")
        print(f"  2. Create {args.config}.py manually")
        print("  3. List configs: python river_monitor.py --list-configs")
        return
    
    # Check if setup is needed
    if check_first_run(config):
        print("\n" + "="*80)
        print("⚠️  FIRST RUN DETECTED")
        print("="*80)
        print("\nNo monitoring sites configured.")
        print("\nWould you like to run the setup wizard? (y/n)")
        print("This will help you find and configure stream gauges to monitor.")
        print("="*80)
        
        response = input("\nRun setup wizard? (y/n): ").strip().lower()
        
        if response == 'y':
            print("\n🚀 Launching setup wizard...\n")
            
            # Import and run setup wizard
            try:
                import setup_wizard
                success = setup_wizard.run_wizard(config_name=args.config)
                
                if not success:
                    print("\n⚠️  Setup not completed. Exiting.")
                    return
                
                # Reload config after setup
                config = load_config(args.config)
                
            except ImportError:
                print("\n✗ Error: setup_wizard.py not found")
                print("Please run: python setup_wizard.py")
                return
            except Exception as e:
                print(f"\n✗ Error during setup: {e}")
                return
        else:
            print(f"\n⚠️  Please configure MONITORING_SITES or LOCATION in {args.config}.py")
            print(f"Or run: python setup_wizard.py --config {args.config}")
            return
    
    # Initialize monitor with the loaded config
    monitor = RiverMonitor(config.MONITORING_SITES, config)
    
    # Store nearby sites for potential display later
    nearby_sites = None
    
    # If location is configured, find nearby sites (but don't display yet)
    if config.LOCATION['latitude'] and config.LOCATION['longitude']:
        nearby_sites = monitor.find_nearby_sites(
            config.LOCATION['latitude'],
            config.LOCATION['longitude'],
            config.SEARCH_RADIUS_MILES
        )
        
        if nearby_sites is not None and len(nearby_sites) > 0:
            # Use the first few sites if none configured
            if not monitor.site_numbers:
                monitor.site_numbers = nearby_sites['site_no'].head(5).tolist()
                print(f"\nMonitoring top {len(monitor.site_numbers)} sites")
    
    # Check if we have sites to monitor
    if not monitor.site_numbers:
        print("\nNo monitoring sites configured!")
        print(f"Please set MONITORING_SITES in {args.config}.py or LOCATION coordinates")
        print("Find sites at: https://waterdata.usgs.gov/")
        return
    
    # Monitor all sites
    results = monitor.monitor_all_sites()
    
    # Print report (nearby sites will only be shown if extreme conditions exist)
    if results:
        monitor.print_report(results, nearby_sites)
    else:
        print("No data available for configured sites")


if __name__ == "__main__":
    main()

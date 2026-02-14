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
    
    def __init__(self, site_numbers=None):
        """
        Initialize the river monitor
        
        Args:
            site_numbers: List of USGS site numbers to monitor
        """
        self.site_numbers = site_numbers or config.MONITORING_SITES
        
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
            radius_dd = radius_miles / 69.0
            
            # Find sites within bounding box
            sites = nwis.what_sites(
                bBox=f"{longitude - radius_dd},{latitude - radius_dd},"
                      f"{longitude + radius_dd},{latitude + radius_dd}",
                parameterCd=config.PARAMETER_CODE,
                siteStatus="active"
            )
            
            print(f"\nFound {len(sites)} active gauges within {radius_miles} miles")
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
            
            df, _ = nwis.get_iv(
                sites=site_number,
                parameterCd=config.PARAMETER_CODE,
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d')
            )
            
            return df
            
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
            start_year = start_year or config.HISTORICAL_START_YEAR
            start_date = f"{start_year}-01-01"
            end_date = datetime.now().strftime('%Y-%m-%d')
            
            df, _ = nwis.get_dv(
                sites=site_number,
                parameterCd=config.PARAMETER_CODE,
                start=start_date,
                end=end_date
            )
            
            return df
            
        except Exception as e:
            print(f"Error getting historical data for site {site_number}: {e}")
            return None
    
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
            
        # Get all historical values
        values = historical_df.iloc[:, 0].values
        values = values[~np.isnan(values)]
        
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
        
        if percentile <= config.VERY_LOW_PERCENTILE:
            return "SEVERE LOW", "Severe drought conditions"
        elif percentile <= config.LOW_FLOW_PERCENTILE:
            return "LOW", "Below normal flow (drought)"
        elif percentile >= config.VERY_HIGH_PERCENTILE:
            return "SEVERE HIGH", "Severe flood conditions"
        elif percentile >= config.HIGH_FLOW_PERCENTILE:
            return "HIGH", "Above normal flow (flood risk)"
        else:
            return "NORMAL", "Normal flow conditions"
    
    def check_site_conditions(self, site_number):
        """
        Check current conditions at a specific site
        
        Args:
            site_number: USGS site number
            
        Returns:
            Dictionary with condition information
        """
        print(f"\nChecking site {site_number}...")
        
        # Get current data
        current_df = self.get_current_data(site_number)
        if current_df is None or len(current_df) == 0:
            return None
        
        # Get most recent value
        current_value = current_df.iloc[-1, 0]
        current_time = current_df.index[-1]
        
        # Get historical data
        historical_df = self.get_historical_data(site_number)
        if historical_df is None:
            return None
        
        # Calculate percentile
        percentile = self.calculate_percentiles(historical_df, current_value)
        severity, description = self.classify_condition(percentile)
        
        # Get statistics
        values = historical_df.iloc[:, 0].values
        values = values[~np.isnan(values)]
        
        result = {
            'site_number': site_number,
            'current_value': current_value,
            'current_time': current_time,
            'percentile': percentile,
            'severity': severity,
            'description': description,
            'historical_min': np.min(values),
            'historical_max': np.max(values),
            'historical_median': np.median(values),
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
    
    def print_report(self, results):
        """
        Print a formatted report of conditions
        
        Args:
            results: List of condition dictionaries
        """
        print("\n" + "="*80)
        print("RIVER LEVEL EXTREME CONDITIONS REPORT")
        print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        extreme_count = 0
        
        for result in results:
            is_extreme = result['severity'] in ['SEVERE LOW', 'SEVERE HIGH', 'LOW', 'HIGH']
            
            if is_extreme:
                extreme_count += 1
                marker = "⚠️ ALERT" if 'SEVERE' in result['severity'] else "⚡ WARNING"
            else:
                marker = "✓"
            
            print(f"\n{marker} Site: {result['site_number']}")
            print(f"  Current Value: {result['current_value']:.2f} cfs")
            print(f"  As of: {result['current_time']}")
            print(f"  Condition: {result['severity']} ({result['description']})")
            print(f"  Percentile: {result['percentile']:.1f}%")
            print(f"  Historical Range: {result['historical_min']:.2f} - "
                  f"{result['historical_max']:.2f} cfs")
            print(f"  Historical Median: {result['historical_median']:.2f} cfs")
        
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
    monitor = RiverMonitor(config.MONITORING_SITES)
    
    # If location is configured, find nearby sites
    if config.LOCATION['latitude'] and config.LOCATION['longitude']:
        print("Finding nearby gauges...")
        nearby_sites = monitor.find_nearby_sites(
            config.LOCATION['latitude'],
            config.LOCATION['longitude'],
            config.SEARCH_RADIUS_MILES
        )
        
        if nearby_sites is not None and len(nearby_sites) > 0:
            print("\nNearby Sites:")
            print(nearby_sites[['site_no', 'station_nm']].to_string())
            
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
    
    # Print report
    if results:
        monitor.print_report(results)
    else:
        print("No data available for configured sites")


if __name__ == "__main__":
    main()

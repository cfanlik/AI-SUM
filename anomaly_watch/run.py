import sys
import os
sys.path.append("/opt/AI-SUM")

from anomaly_watch.anomaly_analyzer import AnomalyAnalyzer
from anomaly_watch.report_generator import AnomalyReportGenerator

def main():
    print("Starting Anomaly Watchlist Scan...")
    analyzer = AnomalyAnalyzer()
    pools = analyzer.analyze()
    
    generator = AnomalyReportGenerator()
    report_path = generator.generate_report(pools)
    print(f"Anomaly report generated successfully: {report_path}")

if __name__ == "__main__":
    main()

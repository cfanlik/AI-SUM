import sys
import os
sys.path.append("/opt/AI-SUM")

from anomaly_watch.anomaly_analyzer import AnomalyAnalyzer
from anomaly_watch.impulse_surge_analyzer import ImpulseSurgeAnalyzer
from anomaly_watch.report_generator import AnomalyReportGenerator

def main():
    print("Starting Anomaly Watchlist Scan...")
    analyzer = AnomalyAnalyzer()
    pools = analyzer.analyze()
    
    generator = AnomalyReportGenerator()
    report_path = generator.generate_report(pools)
    print(f"Anomaly report generated successfully: {report_path}")

    # 新增突发拉伸专报扫描
    print("Starting Impulse Surge Scan...")
    surge_analyzer = ImpulseSurgeAnalyzer()
    surge_results = surge_analyzer.analyze()
    surge_report_path = generator.generate_impulse_surge_report(surge_results)
    print(f"Impulse Surge report generated successfully: {surge_report_path}")

if __name__ == "__main__":
    main()

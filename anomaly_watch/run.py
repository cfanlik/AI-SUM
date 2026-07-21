import sys
sys.path.append('/opt/AI-SUM')

from anomaly_watch.anomaly_analyzer import AnomalyAnalyzer
from anomaly_watch.impulse_surge_analyzer import ImpulseSurgeAnalyzer
from anomaly_watch.report_generator import AnomalyReportGenerator

def main():
    # 1. 运行伪流动性检测
    anomaly_analyzer = AnomalyAnalyzer()
    anomaly_results = anomaly_analyzer.analyze()
    
    # 2. 运行周期性突发吸筹 60d 4阶动量分析
    surge_analyzer = ImpulseSurgeAnalyzer()
    surge_results = surge_analyzer.analyze()

    generator = AnomalyReportGenerator()
    
    # 落盘专报 A：《AI-SUM 伪流动性陷阱与四大物理维度风控专报》 (保持独立存在)
    rep_a = generator.generate_report(anomaly_results)
    print("REPORT_A_GENERATED:", rep_a)

    # 落盘专报 B：《周期性突发吸筹风控专报》 (独立新增)
    rep_b = generator.generate_periodic_impulse_surge_report(surge_results)
    print("REPORT_B_GENERATED:", rep_b)

if __name__ == "__main__":
    main()

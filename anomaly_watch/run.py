import sys
sys.path.append('/opt/AI-SUM')

from anomaly_watch.anomaly_analyzer import AnomalyAnalyzer
from anomaly_watch.impulse_surge_analyzer import ImpulseSurgeAnalyzer
from anomaly_watch.accumulation_top10_analyzer import AccumulationTop10Analyzer
from anomaly_watch.report_generator import AnomalyReportGenerator
from anomaly_watch.dex_penetration_analyzer import run_penetration_analysis
from anomaly_watch.pump_fit_engine import run_pump_fit_analysis

def main():
    # 1. 运行伪流动性检测
    anomaly_analyzer = AnomalyAnalyzer()
    anomaly_results = anomaly_analyzer.analyze()

    # 2. 运行周期性突发吸筹 60d 4阶动量分析
    surge_analyzer = ImpulseSurgeAnalyzer()
    surge_results = surge_analyzer.analyze()

    # 3. 全库真实吸筹候选先计算持币证据，再质量门禁并排序 Top10。
    accumulation_analyzer = AccumulationTop10Analyzer()
    accumulation_run = accumulation_analyzer.analyze(
        surge_results=surge_results,
        fake_liq_results=anomaly_results,
        persist=True,
    )

    # 4. 运行 DEX 庄家资金穿透分析 (含 BUG-1~4 防错算法)
    print("Executing DEX Penetration Analysis...")
    pen_results = run_penetration_analysis()
    print(f"PENETRATION_ANALYSIS_COMPLETED: Analyzed {len(pen_results)} tokens")

    # 5. 运行拉升前兆共振拟合引擎 (3 维拟合降噪)
    print("Executing Pump Resonance Fit Analysis...")
    res_results = run_pump_fit_analysis()
    print(f"PUMP_RESONANCE_COMPLETED: Analyzed {len(res_results)} tokens")

    generator = AnomalyReportGenerator()

    # 落盘专报 A：《AI-SUM 伪流动性陷阱与四大物理维度风控专报》
    rep_a = generator.generate_report(anomaly_results)
    print("REPORT_A_GENERATED:", rep_a)

    # 落盘专报 B：《周期性突发吸筹风控专报》
    rep_b = generator.generate_periodic_impulse_surge_report(accumulation_run)
    print("REPORT_B_GENERATED:", rep_b)

if __name__ == "__main__":
    main()

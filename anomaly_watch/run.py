import sys
sys.path.append('/opt/AI-SUM')
import subprocess

from anomaly_watch.anomaly_analyzer import AnomalyAnalyzer
from anomaly_watch.impulse_surge_analyzer import ImpulseSurgeAnalyzer
from anomaly_watch.accumulation_top10_analyzer import AccumulationTop10Analyzer
from anomaly_watch.report_generator import AnomalyReportGenerator
from anomaly_watch.dex_penetration_analyzer import run_penetration_analysis
from anomaly_watch.pump_fit_engine import run_pump_fit_analysis

def main():
    # 专报生成前触发即时增量物化同步，以获取源端 select.db 最新快照和 Gecko 价格
    print("Executing Real-time Materialize Surge Sync...")
    try:
        subprocess.run(["/usr/bin/python3", "meta-verdict/materialize_surge.py"], 
                       cwd="/opt/AI-SUM", check=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("Warning: materialize_surge sync timed out after 120s, continuing...")
    except Exception as e:
        print(f"Warning: materialize_surge sync failed: {e}, continuing...")

    # 1. 运行伪流动性检测
    anomaly_results = []
    try:
        anomaly_analyzer = AnomalyAnalyzer()
        anomaly_results = anomaly_analyzer.analyze()
    except Exception as e:
        print(f"Error in anomaly_analyzer: {e}")

    # 2. 运行周期性突发吸筹 60d 4阶动量分析
    surge_results = []
    try:
        surge_analyzer = ImpulseSurgeAnalyzer()
        surge_results = surge_analyzer.analyze()
    except Exception as e:
        print(f"Error in surge_analyzer: {e}")

    # 3. 全库真实吸筹候选先计算持币证据，再质量门禁并排序 Top10。
    accumulation_run = None
    try:
        accumulation_analyzer = AccumulationTop10Analyzer()
        accumulation_run = accumulation_analyzer.analyze(
            surge_results=surge_results,
            fake_liq_results=anomaly_results,
            persist=True,
        )
    except Exception as e:
        print(f"Error in accumulation_analyzer: {e}")

    # 4. 运行 DEX 庄家资金穿透分析 (含 BUG-1~4 防错算法)
    try:
        print("Executing DEX Penetration Analysis...")
        pen_results = run_penetration_analysis()
        print(f"PENETRATION_ANALYSIS_COMPLETED: Analyzed {len(pen_results)} tokens")
    except Exception as e:
        print(f"Error in penetration_analysis: {e}")

    # 5. 运行拉升前兆共振拟合引擎 (3 维拟合降噪)
    try:
        print("Executing Pump Resonance Fit Analysis...")
        res_results = run_pump_fit_analysis()
        print(f"PUMP_RESONANCE_COMPLETED: Analyzed {len(res_results)} tokens")
    except Exception as e:
        print(f"Error in pump_fit_analysis: {e}")

    generator = AnomalyReportGenerator()

    # 落盘专报 A：《AI-SUM 伪流动性陷阱与四大物理维度风控专报》
    try:
        rep_a = generator.generate_report(anomaly_results)
        print("REPORT_A_GENERATED:", rep_a)
    except Exception as e:
        print(f"Error generating Report A: {e}")

    # 落盘专报 B：《周期性突发吸筹风控专报》
    try:
        if accumulation_run is not None:
            rep_b = generator.generate_periodic_impulse_surge_report(accumulation_run)
            print("REPORT_B_GENERATED:", rep_b)
    except Exception as e:
        print(f"Error generating Report B: {e}")

    # 运行新版 剧烈突发吸筹风控专报 生成器
    print("Executing Anomaly Intense Surge Report...")
    try:
        subprocess.run(["/usr/bin/python3", "meta-verdict/anomaly_surge_report.py"], cwd="/opt/AI-SUM", check=True)
    except Exception as e:
        print(f"Error executing anomaly_surge_report: {e}")

    # 6. 全局物理专报上限清理 (每个分类最多保留 60 份)
    try:
        from anomaly_watch.report_cleaner import prune_all_aisum_reports
        deleted_cnt = prune_all_aisum_reports()
        print(f"REPORT_PRUNING_COMPLETED: Deleted {deleted_cnt} old report files")
    except Exception as e:
        print(f"Error in report cleaner: {e}")

    # 7. 运行市场实时观察与多维白盒判定报告生成 (Live Observation)
    try:
        print("Executing Live Observation Report Generation...")
        from anomaly_watch.generate_live_observation_report import generate_report
        generate_report()
        print("REPORT_LIVE_OBSERVATION_GENERATED")
    except Exception as e:
        print(f"Error executing generate_live_observation_report: {e}")

if __name__ == "__main__":
    main()

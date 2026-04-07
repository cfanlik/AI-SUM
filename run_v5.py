"""
AI-SUM V5 — 顶层启动脚本
用法：
  python run_v5.py                          # 全库扫描，生成雷达报
  python run_v5.py --watchlist              # 仅查看 Watchlist 状态
  python run_v5.py --token <addr> <chain>   # 单代币深度分析
  python run_v5.py --backtest <addr> <chain># 回溯所有快照（同 --token，无缓存）
  python run_v5.py --dismiss <addr> <chain> # 人工标记 DISMISSED
  python run_v5.py --pumped <addr> <chain>  # 人工标记 PUMPED
  python run_v5.py --note <addr> <chain> "备注" # 添加备注
  python run_v5.py --no-cache               # 全库扫描，强制重算不用缓存
  python run_v5.py --no-report              # 扫描但不保存 MD 报告
"""
import sys
import os

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(__file__))


def main():
    args = sys.argv[1:]

    # ── 全局开关 ──────────────────────────────────────────────
    use_cache   = "--no-cache"  not in args
    save_report = "--no-report" not in args
    # 清除开关参数，不影响后续位置参数解析
    args = [a for a in args if a not in ("--no-cache", "--no-report")]

    # ── 模式路由 ─────────────────────────────────────────────

    if not args:
        # 默认：全库扫描
        from ai_sum_v5.engine import run_full_scan
        run_full_scan(use_cache=use_cache, verbose=True, save_report=save_report)
        return

    cmd = args[0].lower()

    # 仅查看 Watchlist
    if cmd == "--watchlist":
        from ai_sum_v5.engine import show_watchlist
        show_watchlist()
        return

    # 单代币深度分析 / 回溯复盘
    if cmd in ("--token", "--backtest"):
        if len(args) < 3:
            print("用法: python run_v5.py --token <token_address> <chain>")
            sys.exit(1)
        token_address = args[1]
        chain         = args[2]
        from ai_sum_v5.engine import run_single_token
        run_single_token(chain=chain, token_address=token_address, backtest=(cmd == "--backtest"))
        return

    # 人工操作 Watchlist
    if cmd in ("--dismiss", "--pumped"):
        if len(args) < 3:
            print(f"用法: python run_v5.py {cmd} <token_address> <chain>")
            sys.exit(1)
        token_address = args[1]
        chain         = args[2]
        notes         = args[3] if len(args) > 3 else None
        from ai_sum_v5 import db_loader
        from ai_sum_v5.watchlist_tracker import dismiss, mark_pumped
        conn = db_loader.get_connection()
        if cmd == "--dismiss":
            dismiss(conn, chain, token_address, notes)
        else:
            mark_pumped(conn, chain, token_address, notes)
        conn.close()
        return

    if cmd == "--note":
        if len(args) < 4:
            print("用法: python run_v5.py --note <token_address> <chain> \"备注\"")
            sys.exit(1)
        token_address = args[1]
        chain         = args[2]
        note_text     = args[3]
        from ai_sum_v5 import db_loader
        from ai_sum_v5.watchlist_tracker import add_note
        conn = db_loader.get_connection()
        add_note(conn, chain, token_address, note_text)
        conn.close()
        return

    # 未知命令
    print(f"未知命令: {cmd}")
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    main()

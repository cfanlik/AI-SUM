#!/usr/bin/env python3
"""
远程补丁脚本：给 opus/whale/unified 三引擎注入持久化调用
"""
import pathlib

# ── opus-scan 离线模式注入 ──
p = pathlib.Path('/opt/AI-SUM/opus-scan/run.py')
src = p.read_text()

PERSIST_OPUS = (
    '\n        try:\n'
    '            import sys as _sys; _sys.path.insert(0, "/opt/AI-SUM")\n'
    '            from persist_helper import save_opus; save_opus(results)\n'
    '        except Exception as _e:\n'
    '            print(f"  [persist] opus 保存失败: {_e}")\n'
)

target1 = '        path = save_md_leaderboard(results)\n        print(f"  报告已保存: {path}")\n        conn.close()\n        return results'
if target1 in src and '[persist]' not in src[:src.index(target1)+20]:
    src = src.replace(target1,
        '        path = save_md_leaderboard(results)\n        print(f"  报告已保存: {path}")'
        + PERSIST_OPUS
        + '        conn.close()\n        return results', 1)
    p.write_text(src)
    print('[ok] opus离线注入')
else:
    print('[skip] opus离线已注入或未找到')

src = p.read_text()
target2 = '    path = save_md_leaderboard(final_results)\n    print(f"  报告已保存: {path}")\n\n    conn.close()\n    return final_results'
PERSIST_OPUS2 = (
    '\n    try:\n'
    '        import sys as _sys; _sys.path.insert(0, "/opt/AI-SUM")\n'
    '        from persist_helper import save_opus; save_opus(final_results)\n'
    '    except Exception as _e:\n'
    '        print(f"  [persist] opus保存失败: {_e}")\n'
)
if target2 in src and 'final_results)\n    except' not in src:
    src = src.replace(target2,
        '    path = save_md_leaderboard(final_results)\n    print(f"  报告已保存: {path}")'
        + PERSIST_OPUS2
        + '    conn.close()\n    return final_results', 1)
    p.write_text(src)
    print('[ok] opus联网注入')
else:
    print('[skip] opus联网已注入或未找到')

# ── bigcoin/whale 注入 ──
p2 = pathlib.Path('/opt/AI-SUM/bigcoin/run.py')
src2 = p2.read_text()
PERSIST_WHALE = (
    '\n    try:\n'
    '        import sys as _sys; _sys.path.insert(0, "/opt/AI-SUM")\n'
    '        from persist_helper import save_whale; save_whale(results)\n'
    '    except Exception as _e:\n'
    '        print(f"  [persist] whale保存失败: {_e}")\n'
)
target3 = '    path = save_md_radar(results)\n    print(f"\\n  报告已保存: {path}")'
if target3 in src2 and '[persist]' not in src2:
    src2 = src2.replace(target3,
        '    path = save_md_radar(results)\n    print(f"\\n  报告已保存: {path}")'
        + PERSIST_WHALE, 1)
    p2.write_text(src2)
    print('[ok] whale注入')
else:
    # 尝试找实际行
    for i, l in enumerate(src2.split('\n')):
        if 'save_md_radar' in l or 'save_md_whale' in l:
            print(f'  L{i+1}: {repr(l)}')
    print('[warn] whale注入点未找到')

print('done')

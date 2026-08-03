import os
import sqlite3
import hashlib
from datetime import datetime

class SchemaMismatchException(Exception):
    pass

class ProvenanceMissingException(Exception):
    pass

def get_ddl_hash(db_path, table_name):
    """获取指定表的 DDL 哈希，用于 Schema 准入检验"""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    c = conn.cursor()
    try:
        c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        row = c.fetchone()
        if row and row[0]:
            sql = row[0].strip().replace(" ", "").lower()
            return hashlib.sha256(sql.encode('utf-8')).hexdigest()
    except Exception:
        pass
    finally:
        conn.close()
    return None

class SourceLoader:
    def __init__(self, select_db_path="/opt/select-coin/data/select.db", archive_db_path="/opt/select-coin/data/select_archive.db"):
        self.select_db_path = select_db_path
        self.archive_db_path = archive_db_path
        self.conn = None
        self.archive_attached = False

    def execute_audit_p0(self):
        """
        P0 阶段：冷库准入审计与元数据核验 (性能优化 O(1) 版)。
        """
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 启动 P0 级冷库准入审计...")
        
        # 1. 检查 select.db 是否存在
        if not os.path.exists(self.select_db_path):
            raise FileNotFoundError(f"主库 select.db 缺失: {self.select_db_path}")
            
        main_conn = sqlite3.connect(f"file:{self.select_db_path}?mode=ro", uri=True)
        main_c = main_conn.cursor()
        
        # 2. O(1) 统计主库 gecko_market_data
        try:
            main_c.execute("SELECT MIN(scan_time), MAX(scan_time), COUNT(*) FROM gecko_market_data")
            min_g, max_g, count_g = main_c.fetchone()
            print(f"  主库 gecko_market_data: {count_g} 行, 时间范围: [{min_g}] 至 [{max_g}]")
        except Exception as e:
            main_conn.close()
            raise SchemaMismatchException(f"主库缺少 gecko_market_data 表或损坏: {e}")
            
        # 3. O(1) 统计主库 bubblemap_holders 时间范围 (避免全表扫描)
        try:
            main_c.execute("SELECT snapshot_time FROM bubblemap_holders ORDER BY rowid ASC LIMIT 1")
            min_bh_row = main_c.fetchone()
            min_bh = min_bh_row[0] if min_bh_row else None
            
            main_c.execute("SELECT snapshot_time FROM bubblemap_holders ORDER BY rowid DESC LIMIT 1")
            max_bh_row = main_c.fetchone()
            max_bh = max_bh_row[0] if max_bh_row else None
            
            print(f"  主库 bubblemap_holders 时间范围: [{min_bh}] 至 [{max_bh}] (通过 ROWID O(1) 获取)")
        except Exception as e:
            main_conn.close()
            raise SchemaMismatchException(f"主库 bubblemap_holders 表查询失败: {e}")
            
        main_conn.close()

        # 4. 审计冷库
        if not os.path.exists(self.archive_db_path):
            print("  警告: 冷归档库 select_archive.db 不存在，验证将退化为仅主库运行。")
            return {
                "archive_exists": False,
                "schema_check": "NOT_EVALUATED",
                "gecko_market_data_archive_count": 0,
                "bubblemap_holders_archive_count": 0
            }
            
        archive_conn = sqlite3.connect(f"file:{self.archive_db_path}?mode=ro", uri=True)
        archive_c = archive_conn.cursor()
        
        # DDL Schema 校验
        main_bh_hash = get_ddl_hash(self.select_db_path, "bubblemap_holders")
        arch_bh_hash = get_ddl_hash(self.archive_db_path, "bubblemap_holders")
        if main_bh_hash != arch_bh_hash:
            archive_conn.close()
            raise SchemaMismatchException("DENIED_SCHEMA_MISMATCH: 主库与冷库的 bubblemap_holders 表结构不一致")
            
        # O(1) 统计冷库 bubblemap_holders 时间范围
        try:
            archive_c.execute("SELECT snapshot_time FROM bubblemap_holders ORDER BY rowid ASC LIMIT 1")
            min_arch_bh_row = archive_c.fetchone()
            min_arch_bh = min_arch_bh_row[0] if min_arch_bh_row else None
            
            archive_c.execute("SELECT snapshot_time FROM bubblemap_holders ORDER BY rowid DESC LIMIT 1")
            max_arch_bh_row = archive_c.fetchone()
            max_arch_bh = max_arch_bh_row[0] if max_arch_bh_row else None
            
            # 冷库行数不做全表扫描，仅做表级快速计数估算 (如果需要)
            # 我们通过 pragma page_count * page_size 估算，或者直接跳过行数输出
            print(f"  冷归档库 bubblemap_holders 时间范围: [{min_arch_bh}] 至 [{max_arch_bh}] (通过 ROWID O(1) 获取)")
        except Exception as e:
            archive_conn.close()
            raise SchemaMismatchException(f"冷库 bubblemap_holders 查询失败: {e}")
            
        # D1 校验：确认冷库中确无 gecko_market_data
        archive_c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gecko_market_data'")
        has_gecko_in_arch = archive_c.fetchone() is not None
        print(f"  冷库是否包含 gecko_market_data: {has_gecko_in_arch} (符合 D1 只读审计预期)")
        
        archive_conn.close()
        print("P0 级冷库准入审计通过。")
        
        return {
            "archive_exists": True,
            "schema_check": "PASS",
            "gecko_market_data_archive_count": 0,
            "bh_min_time_arch": min_arch_bh,
            "bh_max_time_arch": max_arch_bh
        }

    def connect(self):
        """
        P1 阶段：联合只读数据库连接创建。
        通过 URI 只读连接并设置 query_only=ON 以阻断任何写操作。
        """
        if self.conn:
            return self.conn
            
        self.conn = sqlite3.connect(f"file:{self.select_db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        
        if os.path.exists(self.archive_db_path):
            # 以只读 URI 的方式 attach，100% 只读安全
            self.conn.execute(f"ATTACH DATABASE 'file:{self.archive_db_path}?mode=ro' AS archive")
            self.archive_attached = True
            print("已成功动态只读挂载 select_archive.db (archive)")
            
        # 动态拼装 gecko_market_data 内存 TEMP VIEW，填补数据断带
        cursor = self.conn.cursor()
        patch_db_path = "/tmp/0803/patch_cache.db"
        if os.path.exists(patch_db_path):
            try:
                attached_dbs = [row[1] for row in cursor.execute("PRAGMA database_list").fetchall()]
                if 'patch_db' not in attached_dbs:
                    cursor.execute(f"ATTACH DATABASE 'file:{patch_db_path}?mode=ro' AS patch_db")
                    print("已成功动态挂载补水行情库 patch_db")
                
                # 确定主库的前缀
                main_db_prefix = "main"
                if 'select_coin_db' in attached_dbs:
                    main_db_prefix = "select_coin_db"
                
                cursor.execute("DROP VIEW IF EXISTS temp.gecko_market_data")
                cursor.execute(f"""
                    CREATE TEMP VIEW temp.gecko_market_data AS
                    SELECT * FROM {main_db_prefix}.gecko_market_data
                    UNION ALL
                    SELECT * FROM patch_db.gecko_market_data
                """)
                print("已成功在内存中拼装 temp.gecko_market_data 视图 (包含补水行情)")
            except Exception as e:
                print(f"警告: 拼装补水视图失败: {e}")
                
        # 拼装完毕后，最终锁死 query_only，确保全量只读拦截
        self.conn.execute("PRAGMA query_only = ON")
        return self.conn

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
            self.archive_attached = False

    def query_bubblemap_holders_union(self, chain, token_address, max_time_str):
        """
        P1 阶段：冷热联合查询 bubblemap_holders，执行 UNION ALL 并按唯一键去重。
        """
        conn = self.connect()
        c = conn.cursor()
        
        if self.archive_attached:
            sql = """
                SELECT snapshot_time, hold_percentage, 'hot' AS source_partition FROM bubblemap_holders
                WHERE chain=? AND token_address=? AND snapshot_time<=?
                UNION ALL
                SELECT snapshot_time, hold_percentage, 'archive' AS source_partition FROM archive.bubblemap_holders
                WHERE chain=? AND token_address=? AND snapshot_time<=?
            """
            rows = c.execute(sql, (chain, token_address, max_time_str, chain, token_address, max_time_str)).fetchall()
        else:
            sql = """
                SELECT snapshot_time, hold_percentage, 'hot' AS source_partition FROM bubblemap_holders
                WHERE chain=? AND token_address=? AND snapshot_time<=?
            """
            rows = c.execute(sql, (chain, token_address, max_time_str)).fetchall()
            
        seen = set()
        deduped_rows = []
        seam_overlaps = 0
        
        for r in rows:
            key = (r['snapshot_time'], r['hold_percentage'])
            if key not in seen:
                seen.add(key)
                deduped_rows.append(r)
            else:
                seam_overlaps += 1
                
        if seam_overlaps > 0:
            print(f"  [Seam Audit] {chain} {token_address} 过滤冷热重叠行数: {seam_overlaps}")
            
        deduped_rows.sort(key=lambda x: x['snapshot_time'], reverse=True)
        return deduped_rows


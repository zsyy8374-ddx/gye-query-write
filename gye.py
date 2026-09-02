#!/usr/bin/env python3
"""
🌙 隔夜单股票池 — 交易日9:27 写入通达信 extern_user.txt

建股票池：9:15/9:18/9:20/9:23/9:25 任一时间点 > 0.1
  - 9:15~9:23: 买二*100/自由流通股
  - 9:25: 买一*100/自由流通股(开盘价形成时刻)

写入ID:
  ID=115 → 当天隔夜单 (09:15 买二占比)
  ID=125 → 918占比 (09:18 买二占比)
  ID=126 → 920占比 (09:20 买二占比)
  ID=127 → 923占比 (09:23 买二占比)
  ID=128 → 924占比 (09:24 买二占比)
  ID=116 → 当天开盘占比 (09:25 买一占比)

兼容VBA格式：
  - 代码首位 6/8→1 (沪/科创板)，0/3→0 (深)，9→2 (北交所)
  - 每行: flag|code|ID||value
"""

import sys, os, time, logging

# thsdk 正式账号配置（ths_config.py 软链指向 ~/.openclaw/workspace/ths_config.json，账号 zsyyddx）
_STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
if _STRATEGY_DIR not in sys.path:
    sys.path.insert(0, _STRATEGY_DIR)
from ths_config import get_ths_ops

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("gye")

# ── 路径 ──
EXTERN_FILE = "/mnt/d/GP/通达信金融终端(开心果交易版)V2026/T0002/signals/extern_user.txt"
WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(WORKSPACE)

# ── 5个时间点查询 ──
# 问财列名格式：计算值在 df.columns[0]，股票代码/简称按列名索引
# 9:25 用买一（开盘价形成的核心时刻），其余时间点用买二
TIME_SLOTS = [
    ('9点15分', 115, "9点15分买二*100/自由流通股>0.1"),
    ('9点18分', 125, "9点18分买二*100/自由流通股>0.1"),
    ('9点20分', 126, "9点20分买二*100/自由流通股>0.1"),
    ('9点23分', 127, "9点23分买二*100/自由流通股>0.1"),
    ('9点24分', 128, "9点24分买二*100/自由流通股>0.1"),
    ('9点25分', 116, "9点25分买一*100/自由流通股>0.1"),
]

# 问财限频 250ms，留足余量
WAIT_SEC = 0.35


def get_flag(code: str) -> str:
    """按代码首位判断市场标志：6/8→1, 0/3→0, 9→2"""
    first = code[0]
    if first in ('6', '8'):
        return '1'
    elif first in ('0', '3'):
        return '0'
    elif first == '9':
        return '2'
    return ''


def fetch_all():
    """
    执行6次独立问财查询（带限频延迟），返回:
      data: {code: {name: str, 115: val, 125: val, 126: val, 127: val, 128: val, 116: val}}
      query_counts: {id: count}
    """
    from thsdk import THS

    ops = get_ths_ops()
    if ops:
        log.info("使用正式账号: %s", ops.get('username'))
    else:
        log.warning("未找到正式账号配置，将使用游客模式")

    data = {}
    query_counts = {}

    with THS(ops) as ths:
        for i, (time_key, id_val, query) in enumerate(TIME_SLOTS):
            if i > 0:
                log.info("等待 %.0fms 避限频...", WAIT_SEC * 1000)
                time.sleep(WAIT_SEC)

            log.info("查询 %s (ID=%d): %s", time_key, id_val, query)
            resp = ths.wencai_nlp(query)
            if not resp or resp.df is None or resp.df.empty:
                log.warning("   %s 无结果", time_key)
                query_counts[id_val] = 0
                continue

            df = resp.df
            log.info("   列名: %s", list(df.columns[:3]))

            # 计算值在第0列，股票代码/简称按列名索引
            ratio_col = df.columns[0]
            log.info("   比值列: %s", ratio_col)

            count = 0
            for _, row in df.iterrows():
                code_raw = row['股票代码']
                name = row['股票简称']
                val = row[ratio_col]

                code = str(code_raw).split('.')[0]
                if code not in data:
                    data[code] = {'name': name}
                data[code][id_val] = val
                count += 1

            query_counts[id_val] = count
            log.info("   查到 %d 只", count)

    return data, query_counts


def build_lines(data: dict) -> tuple:
    """生成 extern_user.txt 行，每只股票最多5行"""
    lines = []
    ALL_IDS = (115, 125, 126, 127, 128, 116)
    count_by_id = {id_val: 0 for id_val in ALL_IDS}
    nan_set = set()
    zero_set = set()

    for code, info in data.items():
        flag = get_flag(code)
        if not flag:
            continue

        for id_val in ALL_IDS:
            val = info.get(id_val)
            if val is None:
                # 统计哪些代码缺哪些时点
                if id_val not in nan_set:
                    pass  # 太多了不打日志
                continue
            # float 且 (0 或 NaN) → 跳过
            if isinstance(val, float):
                if val != val:  # NaN
                    nan_set.add((code, id_val))
                    continue
                if val == 0:
                    zero_set.add((code, id_val))
                    continue

            val_str = f"{val:.6f}" if isinstance(val, (int, float)) else str(val)
            lines.append(f"{flag}|{code}|{id_val}||{val_str}")
            count_by_id[id_val] += 1

    # 汇总缺失信息
    if nan_set:
        log.warning("NaN值详情(%d条):", len(nan_set))
        for code, id_val in sorted(nan_set)[:5]:
            log.warning("    %s ID=%d 为NaN", code, id_val)
        if len(nan_set) > 5:
            log.warning("    ... 共 %d 条", len(nan_set))
    if zero_set:
        log.warning("0值跳过(%d条):", len(zero_set))
        for code, id_val in sorted(zero_set)[:5]:
            log.warning("    %s ID=%d 为0", code, id_val)
        if len(zero_set) > 5:
            log.warning("    ... 共 %d 条", len(zero_set))

    return lines, count_by_id


def write_to_file(lines: list) -> bool:
    """追加到 extern_user.txt，替换旧行"""
    if not os.path.exists(EXTERN_FILE):
        log.error("文件不存在: %s", EXTERN_FILE)
        return False

    with open(EXTERN_FILE, 'rb') as f:
        raw = f.read()
    try:
        text = raw.decode('gbk')
    except UnicodeDecodeError:
        text = raw.decode('gbk', errors='replace')

    target_ids = {115, 125, 126, 127, 128, 116}
    original = text.split('\n')
    kept = [l for l in original
            if not (len(l.split('|')) >= 3
                    and l.split('|')[2].isdigit()
                    and int(l.split('|')[2]) in target_ids)]

    kept.extend(lines)
    new_text = '\n'.join(kept)

    with open(EXTERN_FILE, 'wb') as f:
        f.write(new_text.encode('gbk', errors='replace'))

    return True


def main():
    log.info("隔夜单股票池写入开始（5次独立查询 + 限频延迟）...")

    try:
        data, query_counts = fetch_all()
    except Exception as e:
        log.error("获取数据失败: %s", e, exc_info=True)
        return 1

    if not data:
        log.warning("没有符合隔夜单条件的股票")
        return 1

    lines, count_by_id = build_lines(data)
    total = len(lines)

    if total == 0:
        log.warning("没有有效隔夜单数据可写入（全部为NaN/0？）")
        return 1

    ok = write_to_file(lines)
    if not ok:
        return 1

    log.info("=" * 50)
    log.info("写入 extern_user.txt 共 %d 条", total)
    id_names = {115: '当天隔夜单(09:15买二)', 125: '918占比(09:18买二)', 126: '920占比(09:20买二)', 127: '923占比(09:23买二)', 128: '924占比(09:24买二)', 116: '当天开盘占比(09:25买一)'}
    for id_val in (115, 125, 126, 127, 128, 116):
        src = query_counts.get(id_val, 0)
        written = count_by_id.get(id_val, 0)
        log.info("   ID=%d (%s): 查到 %d 只, 写入 %d 条",
                 id_val, id_names[id_val], src, written)
    log.info("股票池共 %d 只", len(data))

    # 隔夜单占比(ID=115,09:15买二)前5名写入复盘板块(保留已有个股)
    log.info("隔夜单占比(ID=115) TOP5写入复盘板块...")
    sorted_by_115 = []
    for code, info in data.items():
        val115 = info.get(115)
        if isinstance(val115, (int, float)) and val115 > 0:
            sorted_by_115.append((code, info.get('name', ''), val115))
    sorted_by_115.sort(key=lambda x: x[2], reverse=True)
    top5 = sorted_by_115[:5]

    for code, name, val in top5:
        log.info("   %s %s 隔夜单占比=%.2f%%", code, name, val)

    top5_codes = [c for c, _, _ in top5]
    if top5_codes:
        try:
            sys.path.insert(0, '/home/ddx/.openclaw/skills/tdx-custom-block')
            from tdx_block import read_block, write_block, register_block
            existing = read_block('FUPAN') or []
            merged = list(dict.fromkeys(existing + top5_codes))
            if set(merged) != set(existing):
                write_block('FUPAN', merged)
                register_block('复盘', 'FUPAN')
                log.info("复盘板块更新: %d->%d只, +%d只(隔夜单前5)", len(existing), len(merged), len(merged)-len(existing))
            else:
                log.info("复盘板块未变化: %d只", len(existing))
        except Exception as e:
            log.error("复盘板块写入失败: %s", e)

    return 0


if __name__ == '__main__':
    sys.exit(main())

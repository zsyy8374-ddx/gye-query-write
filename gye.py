#!/usr/bin/env python3
"""
🌙 隔夜单股票池 — 交易日9:27 写入通达信 extern_user.txt + 101序列 + 129外部数据

v2.1 (2026-09-05): 新增 ID=129「竞价当天最大占比」外部单值数据
  与101同源同值（6时点最大值），但以 extern_user.txt 单值行写入（同115/125一类），
  datacfg.dat 注册 type=0 外部（字符串/数值），重启通达信后在外部数据管理可见。

v2.0 (2026-09-05): 新增 ID=101「竞价最大占比」(日期-数值序列)
  对每只股票取 隔夜单(9:15)/918/920/923/924/开盘(9:25) 六时点占比的最大值，
  写入 signals_user_101/{市场标志}_{代码}.dat（每条8字节: 日期uint32小端 + 数值float32小端）
  日期 = 数据所在交易日(运行日, 可传参覆盖)，同日重复记录自动替换，按日期升序排列

建股票池：9:15/9:18/9:20/9:23/9:25 任一时间点 > 0.1
  - 9:15~9:23: 买二*100/自由流通股
  - 9:25: 买一*100/自由流通股(开盘价形成时刻)

写入ID:
  ID=115 → 当天隔夜单 (09:15 买二占比)  ← 核心
  ID=125 → 918占比 (09:18 买二占比)
  ID=126 → 920占比 (09:20 买二占比)
  ID=127 → 923占比 (09:23 买二占比)
  ID=128 → 924占比 (09:24 买二占比)
  ID=116 → 当天开盘占比 (09:25 买一占比)
  ID=101 → 竞价最大占比 (上述6时点中的最大值, 日期-数值序列)
  ID=129 → 竞价当天最大占比 (同上最大值, 外部单值行)

兼容VBA格式：
  - 代码首位 6/8→1 (沪/科创板)，0/3→0 (深)，9→2 (北交所)
  - 每行: flag|code|ID||value

用法: python3 gye.py [YYYYMMDD]   # 可传日期参数(写入101用)，默认当天
"""

import sys, os, time, logging, struct
from datetime import datetime

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
SIGNALS_101_DIR = "/mnt/d/GP/通达信金融终端(开心果交易版)V2026/T0002/signals/signals_user_101"
DATACFG_FILE = "/mnt/d/GP/通达信金融终端(开心果交易版)V2026/T0002/signals/datacfg.dat"

# ── ID=101 竞价最大占比（signals_user_101 日期-数值序列） ──
SIGNALS_101_ID = 101
SIGNALS_101_NAME = "竞价最大占比"
# 取最大值的6个来源时点: 隔夜单(核心)/918/920/923/924/开盘
SRC_IDS_101 = (115, 125, 126, 127, 128, 116)

# ── ID=129 竞价当天最大占比（外部单值数据, extern_user.txt） ──
EXT_ID_129 = 129
EXT_NAME_129 = "竞价当天最大占比"

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
    count_by_id[EXT_ID_129] = 0
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


def build_lines_129(data: dict) -> list:
    """生成 ID=129 竞价当天最大占比行：flag|code|129||最大值（与101同源）"""
    lines = []
    for code, info in data.items():
        flag = get_flag(code)
        if not flag:
            continue
        mx = max_bid_ratio(info)
        if mx is None:
            continue
        lines.append(f"{flag}|{code}|{EXT_ID_129}||{mx:.6f}")
    return lines


# ══════════════════ ID=101 竞价最大占比（signals_user_101 序列） ══════════════════

def max_bid_ratio(info: dict):
    """取6时点占比最大值: 隔夜单(核心)/918/920/923/924/开盘；忽略NaN；全空返回None"""
    vals = []
    for idv in SRC_IDS_101:
        v = info.get(idv)
        if isinstance(v, (int, float)) and v == v:  # 排除NaN
            vals.append(float(v))
    return max(vals) if vals else None


def resolve_date(argv: list) -> int:
    """101写入用日期: 支持 python3 gye.py YYYYMMDD，默认当天"""
    if len(argv) > 1 and argv[1].isdigit() and len(argv[1]) == 8:
        return int(argv[1])
    return int(datetime.now().strftime('%Y%m%d'))


def write_signals_101(data: dict, date_int: int, dry_run: bool = False):
    """写入 signals_user_101/{flag}_{code}.dat：每条8字节 = 日期(uint32小端,yyyymmdd) + 数值(float32小端)

    - 文件不存在 → 新建；已存在 → 保留历史记录
    - 同日记录 → 替换；其余按日期升序重排
    返回 (写入只数, 替换只数, 跳过只数)
    """
    if dry_run:
        log.info("[DRY-RUN] signals_user_101 预览 (date=%d, dir=%s)", date_int, SIGNALS_101_DIR)
    written = replaced = skipped = 0
    try:
        os.makedirs(SIGNALS_101_DIR, exist_ok=True)
    except Exception as e:
        log.error("无法访问目录 %s: %s", SIGNALS_101_DIR, e)
        return 0, 0, 0

    for code, info in data.items():
        flag = get_flag(code)
        if not flag:
            continue
        mx = max_bid_ratio(info)
        if mx is None:
            skipped += 1
            continue
        fname = f"{flag}_{code}.dat"
        fpath = os.path.join(SIGNALS_101_DIR, fname)
        name = info.get('name', '')
        if dry_run:
            log.info("    %s %-6s → %s : %.4f", code, name, fname, mx)
            written += 1
            continue
        # 读旧记录
        recs = []
        if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
            try:
                raw = open(fpath, 'rb').read()
                n = len(raw) // 8
                if len(raw) % 8:
                    log.warning("    %s 长度非8倍数(%dB)，尾部%d字节截断", fpath, len(raw), len(raw) % 8)
                for i in range(n):
                    d8, v8 = struct.unpack('<If', raw[i * 8:i * 8 + 8])
                    recs.append([d8, v8])
            except Exception as e:
                log.error("    %s 解析失败: %s", fpath, e)
                continue
        # 同日替换
        if any(r[0] == date_int for r in recs):
            replaced += 1
            recs = [r for r in recs if r[0] != date_int]
        recs.append([date_int, mx])
        recs.sort(key=lambda r: r[0])
        try:
            with open(fpath, 'wb') as f:
                for d8, v8 in recs:
                    f.write(struct.pack('<If', int(d8), float(v8)))
            written += 1
        except Exception as e:
            log.error("    %s 写入失败: %s", fpath, e)
    return written, replaced, skipped


def ensure_datacfg_101_name():
    """把 datacfg.dat 中 ID=101 名称注册为「竞价最大占比」（120B/条, 名字区 offset 8..56）"""
    if not os.path.exists(DATACFG_FILE):
        log.warning("datacfg.dat 不存在，跳过101名称注册")
        return
    with open(DATACFG_FILE, 'rb') as f:
        d = bytearray(f.read())
    found = False
    name_gbk = SIGNALS_101_NAME.encode('gbk')
    for i in range(len(d) // 120):
        off = i * 120
        if struct.unpack('<I', d[off:off + 4])[0] != SIGNALS_101_ID:
            continue
        # 名字区清零后写入（保留 56..60 时间戳 / 60..64 ref 不动）
        for j in range(off + 8, off + 56):
            d[j] = 0
        d[off + 8:off + 8 + len(name_gbk)] = name_gbk
        found = True
        break
    if not found:
        log.error("datacfg.dat 中未找到 ID=%d，跳过名称注册", SIGNALS_101_ID)
        return
    with open(DATACFG_FILE, 'wb') as f:
        f.write(d)
    log.info("datacfg.dat: ID=%d 名称已注册为「%s」", SIGNALS_101_ID, SIGNALS_101_NAME)


def write_to_file(lines: list, extra_lines: list = None) -> bool:
    """追加到 extern_user.txt，替换旧行（extra_lines 为 ID=129 行，一并写入）"""
    if extra_lines is None:
        extra_lines = []
    if not os.path.exists(EXTERN_FILE):
        log.error("文件不存在: %s", EXTERN_FILE)
        return False

    with open(EXTERN_FILE, 'rb') as f:
        raw = f.read()
    try:
        text = raw.decode('gbk')
    except UnicodeDecodeError:
        text = raw.decode('gbk', errors='replace')

    target_ids = {115, 125, 126, 127, 128, 116, EXT_ID_129}
    original = text.split('\n')
    kept = [l for l in original
            if not (len(l.split('|')) >= 3
                    and l.split('|')[2].isdigit()
                    and int(l.split('|')[2]) in target_ids)]

    kept.extend(lines)
    kept.extend(extra_lines)
    new_text = '\n'.join(kept)

    with open(EXTERN_FILE, 'wb') as f:
        f.write(new_text.encode('gbk', errors='replace'))

    return True


def ensure_datacfg_129():
    """确保 datacfg.dat 中 ID=129「竞价当天最大占比」已注册（type=0 外部单值, ref=130）

    若 129 记录已存在则不动（避免覆盖通达信运行中写入的内容），仅当缺失时插入到 127 之后。
    """
    if not os.path.exists(DATACFG_FILE):
        log.warning("datacfg.dat 不存在，跳过129注册")
        return
    with open(DATACFG_FILE, 'rb') as f:
        d = bytearray(f.read())
    name_gbk = EXT_NAME_129.encode('gbk')
    # 检查是否已注册
    for i in range(len(d) // 120):
        off = i * 120
        if struct.unpack('<I', d[off:off + 4])[0] == EXT_ID_129:
            cur = d[off + 8:off + 60].split(b'\x00')[0]
            try:
                cur_s = cur.decode('gbk')
            except Exception:
                cur_s = repr(cur)
            if cur_s == EXT_NAME_129:
                log.info("datacfg.dat: ID=%d「%s」已注册，跳过", EXT_ID_129, EXT_NAME_129)
            else:
                log.warning("datacfg.dat: ID=%d 已存在但名称=%r，未覆盖", EXT_ID_129, cur_s)
            return
    # 未注册 → 插入到 ID=127 记录之后
    insert_off = None
    for i in range(len(d) // 120):
        off = i * 120
        if struct.unpack('<I', d[off:off + 4])[0] == 127:
            insert_off = (i + 1) * 120
            break
    if insert_off is None:
        log.error("datacfg.dat 中未找到 ID=127，无法定位129插入点")
        return
    rec = bytearray(120)
    struct.pack_into('<I', rec, 0, EXT_ID_129)
    struct.pack_into('<I', rec, 4, 0)  # type=0 外部单值
    rec[8:8 + len(name_gbk)] = name_gbk
    struct.pack_into('<I', rec, 60, EXT_ID_129 + 1)  # ref=130
    new_d = bytearray(d[:insert_off]) + rec + bytearray(d[insert_off:])
    with open(DATACFG_FILE, 'wb') as f:
        f.write(new_d)
    log.info("datacfg.dat: 已插入 ID=%d「%s」注册记录", EXT_ID_129, EXT_NAME_129)


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

    # ID=129 竞价当天最大占比行（与101同源同值）
    lines_129 = build_lines_129(data)

    ok = write_to_file(lines, lines_129)
    if not ok:
        return 1

    log.info("=" * 50)
    log.info("写入 extern_user.txt 共 %d 条 (含129: %d 条)", total, len(lines_129))
    id_names = {115: '当天隔夜单(09:15买二)', 125: '918占比(09:18买二)', 126: '920占比(09:20买二)', 127: '923占比(09:23买二)', 128: '924占比(09:24买二)', 116: '当天开盘占比(09:25买一)', EXT_ID_129: '竞价当天最大占比'}
    for id_val in (115, 125, 126, 127, 128, 116, EXT_ID_129):
        if id_val == EXT_ID_129:
            log.info("   ID=%d (%s): 取6时点最大值, 写入 %d 条",
                     id_val, id_names[id_val], len(lines_129))
            continue
        src = query_counts.get(id_val, 0)
        written = count_by_id.get(id_val, 0)
        log.info("   ID=%d (%s): 查到 %d 只, 写入 %d 条",
                 id_val, id_names[id_val], src, written)
    log.info("股票池共 %d 只", len(data))

    # ── ID=101 竞价最大占比 → signals_user_101 序列（日期-数值） ──
    date_int = resolve_date(sys.argv)
    log.info("ID=%d「%s」写入 signals_user_101 (date=%d, 取6时点最大值)...",
             SIGNALS_101_ID, SIGNALS_101_NAME, date_int)
    w101, r101, s101 = write_signals_101(data, date_int)
    ensure_datacfg_101_name()
    ensure_datacfg_129()
    log.info("ID=101 竞价最大占比: 写入%d只(替换同日%d只, 跳过无有效值%d只)", w101, r101, s101)
    log.info("⚠️ 重启通达信后可在 自定义数据101 查看（重启前勿覆盖本文件）")

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

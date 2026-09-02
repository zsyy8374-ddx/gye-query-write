#!/usr/bin/env python3
"""
update_markdat.py (v2) — 涨停原因写入: extern_user.txt(ID=108) + mark.dat
改动：主力→通达信问小达HTTP API，备选→thsdk（同花顺问财）

写入文件:
  1. ~/tdx/T0002/signals/extern_user.txt  (自定义外部数据ID=108)
  2. /mnt/.../mark.dat                    (标记分值=7 + TIP内容)
  3. extdata_import_108.txt               (备用导入文件)

extern_user.txt 格式:
  {市场}|{6位代码}|108|{原因摘要, [行业]}|0.00
  市场: 0=深市 1=沪市 2=北交所

改动总结（2026-07-03）:
  主力: 通达信问小达HTTP API (TQL), PAGE_SIZE=100, 2页搞定
  备选: thsdk 问财 (同花顺)
  自动校验去重
"""
import sys, os, re, logging, shutil, json, time
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("markdat")

DEFAULT_EXTERN = os.path.expanduser("~/tdx/T0002/signals/extern_user.txt")
DEFAULT_EXTERN_BAK = os.path.expanduser("~/tdx/T0002/signals/extern_user.txt.bak")
DEFAULT_MARK_DAT = "/mnt/d/GP/通达信金融终端(开心果交易版)V2026/T0002/mark.dat"
DEFAULT_MARK_BAK = "/mnt/d/GP/通达信金融终端(开心果交易版)V2026/T0002/mark - 副本.dat"

CUSTOM_DATA_ID = "108"
CUSTOM_DATA_NAME = "涨停原因"
PAGE_SIZE = 100
TQL_BASE = "https://wenda.tdx.com.cn/TQL"


def get_market(code: str) -> str:
    first = code[0]
    if first == '6':
        return '1'
    elif first in '03':
        return '0'
    elif first in '489':
        return '2'
    return '0'


def build_text(company: str, reason: str, industry: str) -> str:
    """构建自定义数据文本: 原因摘要, [行业]"""
    parts = []
    if reason.strip():
        r = reason.strip()
        # 1) 移除免责声明(保留正文)
        r = re.sub(r'[（(]免责声明.*', '', r, flags=re.DOTALL).strip()
        # 2) 通达信分隔符I(题材概述与编号明细之间) → 分号; 只匹配前后都不是字母的独立大写I, 不误伤AI/iPhone/API
        r = re.sub(r'(?<![A-Za-z])I(?![A-Za-z])', '; ', r)
        # 3) 替换换行为分号
        r = r.replace('\r\n', '; ').replace('\r', '; ').replace('\n', '; ')
        # 4) 保留编号条目(1、2、...), 不做删除, 只统一编号符号: 行首"1."→"1、"
        r = re.sub(r'(?<=\s)\d+\.\s+', lambda m: m.group(0).replace('.', '、'), r)
        r = re.sub(r'^\d+\.\s+', lambda m: m.group(0).replace('.', '、'), r)
        # 5) 清理多余分号(不减内容)
        r = re.sub(r'[；;][；;\s]*', '; ', r)
        r = re.sub(r'\s*;\s*', '; ', r).strip('; ')
        # 6) 清理多余空格
        r = re.sub(r'\s+', ' ', r).strip()
        if r:
            parts.append(r)
    if industry:
        ind_short = industry.rsplit('-', 1)[-1] if '-' in industry else industry
        parts.append(f"[{ind_short}]")
    return ", ".join(parts)


def get_key(code: str) -> str:
    first = code[0]
    if first == '6':
        return f"01{code}"
    elif first in '03':
        return f"00{code}"
    elif first in '489':
        return f"02{code}"
    return f"00{code}"


def update_markdat(mark_path, bak_path, stocks, tip_date, short_date_tag):
    """更新mark.dat: [MARK]=7 + [TIP]内容"""
    CRLF = b'\r\n'
    src = mark_path if os.path.exists(mark_path) else bak_path
    if not os.path.exists(src):
        logger.warning(f"mark.dat不存在: {src}, 跳过")
        return
    with open(src, 'rb') as f:
        bak = f.read()

    our_keys = {get_key(s['code']) for s in stocks}

    tip_pos = bak.find(b'\r\n[TIP]\r\n')
    if tip_pos < 0:
        logger.warning("mark.dat中未找到[TIP]段, 跳过")
        return

    mark_start = bak.find(b'[MARK]')
    if mark_start < 0:
        logger.warning("mark.dat中未找到[MARK]段, 跳过")
        return
    file_prefix = bak[:mark_start]
    mark_section_start = mark_start + len(b'[MARK]\r\n')
    mark_section_bytes = bak[mark_section_start:tip_pos]

    mark_cleaned = []
    for line in mark_section_bytes.split(CRLF):
        if not line:
            continue
        skip = False
        for key in our_keys:
            if line.startswith(key.encode()):
                skip = True
                break
        if not skip:
            mark_cleaned.append(line)

    head_prefix = file_prefix + b'[MARK]\r\n' + CRLF.join(mark_cleaned) + CRLF if mark_cleaned else file_prefix + b'[MARK]\r\n'

    after_tip = bak.find(CRLF, tip_pos + 6)
    tip_body = bak[after_tip + len(CRLF):]
    tip_cleaned = []
    for line in tip_body.split(CRLF):
        if not line:
            continue
        skip = False
        for key in our_keys:
            if line.startswith(key.encode()):
                skip = True
                break
        if not skip:
            tip_cleaned.append(line)

    new_scores = b''
    for s in stocks:
        new_scores += f"{get_key(s['code'])}=7\r\n".encode('gbk')

    new_tips = b''
    for s in stocks:
        text = build_text(s['name'], s['reason'], s['industry'])
        key = get_key(s['code'])
        line = f"{key}={tip_date} {s['code']}{s['name']}-{short_date_tag}-{text} -\r\n"
        new_tips += line.encode('gbk', errors='replace')

    content = head_prefix + new_scores + b'\r\n[TIP]\r\n'
    content += CRLF.join(tip_cleaned) + CRLF if tip_cleaned else b''
    content += new_tips
    if not content.endswith(CRLF):
        content += CRLF

    with open(mark_path, 'wb') as f:
        f.write(content)

    lines = [l for l in content.split(b'\r\n') if l]
    tips = sum(1 for l in lines if short_date_tag.encode() in l and b'-' in l)
    print(f"mark.dat写入: {len(content)/1024:.0f}KB")
    print(f"  [MARK]=7: {len(stocks)}条, [TIP]: {tips}条")


def write_extern_user(extern_path, stocks, tip_date):
    """写入 extern_user.txt"""
    CRLF = '\r\n'
    old_lines = []
    if os.path.exists(extern_path):
        with open(extern_path, 'r', encoding='gbk', errors='replace') as f:
            old_lines = f.read().splitlines(keepends=True)
    else:
        old_lines = []

    kept_lines = []
    removed_count = 0
    for line in old_lines:
        stripped = line.strip()
        if f'|{CUSTOM_DATA_ID}|' in stripped:
            removed_count += 1
            continue
        kept_lines.append(line)

    new_lines = []
    for s in stocks:
        market = get_market(s['code'])
        text = build_text(s['name'], s['reason'], s['industry'])
        text = text.replace('|', '-')
        line = f"{market}|{s['code']}|{CUSTOM_DATA_ID}|{text}|0.00{CRLF}"
        new_lines.append(line)

    content = ''.join(kept_lines) + ''.join(new_lines)
    with open(extern_path, 'w', encoding='gbk', errors='replace') as f:
        f.write(content)

    print(f"\n写入: {extern_path}")
    print(f"  移除旧ID=108: {removed_count}条")
    print(f"  新增ID=108今日: {len(new_lines)}条")
    print(f"  文件总大小: {len(content.encode('gbk', errors='replace'))/1024:.0f}KB")

    out_path = "extdata_import_108.txt"
    with open(out_path, 'w', encoding='gbk', errors='replace') as f:
        f.write(f"# 通达信自定义外部数据 ID=108 涨停原因{CRLF}")
        f.write(f"# 日期: {tip_date} | 共{len(stocks)}只{CRLF}")
        for s in stocks:
            market = get_market(s['code'])
            text = build_text(s['name'], s['reason'], s['industry'])
            f.write(f"{market}|{s['code']}|{CUSTOM_DATA_ID}|{text}|0.00{CRLF}")
    print(f"  备用文件: {os.path.abspath(out_path)}")


# ========== 数据源：通达信问小达HTTP API（主力） ==========

def _tdx_page_api(page, entry, ri, body_list):
    """通过Playwright页面JS上下文调用TQL API"""
    return json.loads(page.evaluate(f"""
        async () => {{
            const resp = await fetch('/TQL?Entry={entry}&RI={ri}', {{
                method: 'POST',
                headers: {{'Content-Type': 'text/plain; charset=UTF-8'}},
                body: JSON.stringify({json.dumps(body_list)})
            }});
            return await resp.text();
        }}
    """))

def _fetch_via_tdx_tql(trade_date_str=None):
    """
    通达信问小达HTTP API获取涨停数据
    trade_date_str: 日期如"2026/07/03"，None表示今日
    返回: [{"code", "name", "reason", "industry"}, ...] 或 None
    """
    from playwright.sync_api import sync_playwright

    # 构造查询消息
    msg = "涨停板块"
    if trade_date_str:
        # 尝试查历史日期（格式自由，问小达能解析）
        msg = f"{trade_date_str} 涨停板块"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = ctx.new_page()
            page.goto("https://wenda.tdx.com.cn/site/wenda/stock_index.html", wait_until="load", timeout=30000)
            time.sleep(3)

            # 1) StockSelect
            r1 = _tdx_page_api(page, "NLPSE.StockSelect", "",
                [{"message": msg, "TDXID": "", "wdbk": "", "RANG": "AG", "forward": "1", "rang_message": ""}])
            ri = r1[0][3]
            nlpse_id = r1[3][0]

            # 2) SmartQuery — 绑定session
            _tdx_page_api(page, "NLPSE.SmartQuery", ri,
                [{"op_flag": 1, "order_field": "", "order_flag": 1, "cond_json": "", "POS": 0, "COUNT": -1, "RANG": "AG"}])

            # 3) NLPCheckDelete — 轮询等查询完成
            total = 0
            for attempt in range(10):
                r3 = _tdx_page_api(page, "NLPSE.NLPCheckDelete", ri,
                    [{"nlpse_id": nlpse_id, "op_flag": 1, "screen_type": 1, "RANG": "AG", "forward": "1"}])
                try:
                    check = json.loads(r3[3][0])
                    matched = int(check.get('MATCH', 0))
                    if matched > 0:
                        total = matched
                        logger.info(f"TQL: MATCH={total} (attempt {attempt+1})")
                        break
                except:
                    pass
                time.sleep(1)
            else:
                logger.warning("TQL: 查询超时")
                browser.close()
                return None

            # 4) NLPQuery — 分页拿数据
            all_rows = []
            for pos in range(0, total, PAGE_SIZE):
                count = min(PAGE_SIZE, total - pos)
                r4 = _tdx_page_api(page, "NLPSE.NLPQuery", ri,
                    [{"nlpse_id": nlpse_id, "op_flag": 1, "POS": pos, "COUNT": count, "RANG": "AG"}])
                rows = r4[3:]
                all_rows.extend(rows)

            # 5) 去重校验
            unique = {str(r[2]) for r in all_rows}
            if len(unique) != total:
                logger.warning(f"TQL: 去重数({len(unique)}) ≠ total({total})")

            stocks = []
            for row in all_rows:
                stocks.append({
                    "code": str(row[2]),
                    "name": str(row[3]),
                    "reason": str(row[15]) if len(row) > 15 else "",  # 涨停原因揭秘长文本(第15列, 第16列是板型)
                    "industry": str(row[6]).replace('@', '') if len(row) > 6 else ""  # 所属行业
                })

            logger.info(f"TQL: {len(stocks)}只涨停股")
            browser.close()
            return stocks

    except Exception as e:
        logger.warning(f"TQL API失败: {e}")
        return None


# ========== 数据源：thsdk 问财（备选） ==========

def _fetch_via_thsdk(q):
    """thsdk问财查询（备选）"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sector_eval/data_sources"))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("thsdk_feed",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "sector_eval/data_sources/thsdk_feed.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ths = mod._get_ths()
        r = ths.wencai_nlp(q)
        zt = r.data if r and r.data and isinstance(r.data, list) else []
        result = []
        for item in zt:
            c = str(item.get("股票代码", "")).strip().replace(".SZ", "").replace(".SH", "")
            n = str(item.get("股票简称", "")).strip()
            yy = ""
            for k in item:
                if "涨停原因揭秘" in k:
                    yy = str(item[k]).strip()
                    break
            hy = str(item.get("所属同花顺行业", "")).strip()
            if c.isdigit() and len(c) == 6:
                result.append({"code": c, "name": n, "reason": yy, "industry": hy})
        ths.__exit__(None, None, None)
        return result
    except Exception as e:
        logger.warning(f"thsdk查询失败: {e}")
        return []


def _last_trade_day() -> datetime:
    dt = datetime.now()
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt


def main():
    import argparse
    p = argparse.ArgumentParser(description="涨停原因写入: extern_user.txt(ID=108) + mark.dat")
    p.add_argument("--extern", default=DEFAULT_EXTERN, help="extern_user.txt路径")
    p.add_argument("--mark-dat", default=DEFAULT_MARK_DAT, help="mark.dat路径")
    p.add_argument("--mark-bak", default=DEFAULT_MARK_BAK, help="mark.dat副本路径")
    p.add_argument("--yesterday", action="store_true", help="使用前一天数据")
    p.add_argument("--thsdk", action="store_true", help="强制使用thsdk（跳过TQL API）")
    a = p.parse_args()

    now = datetime.now()
    base = _last_trade_day() if now.weekday() >= 5 else now
    if a.yesterday:
        td = base - timedelta(days=1)
        while td.weekday() >= 5:
            td -= timedelta(days=1)
    else:
        td = base

    td_str = td.strftime("%Y/%-m/%-d")
    short_tag = td.strftime("%y%m%d")

    stocks = []

    # 主力：TQL API（除非强制thsdk）
    if not a.thsdk:
        logger.info(f"TQL: 查询涨停板块 ({td_str})...")
        stocks = _fetch_via_tdx_tql(td_str)
        if stocks:
            logger.info(f"TQL主力完成: {len(stocks)}只")

    # 备选：thsdk
    if not stocks:
        q = f"{td_str} 涨停 股票代码 股票简称 涨停原因揭秘 所属同花顺行业"
        logger.info(f"thsdk备选: {q[:60]}...")
        stocks = _fetch_via_thsdk(q)
        if stocks:
            logger.info(f"thsdk备选完成: {len(stocks)}只")

    if not stocks:
        print("无数据（主备均失败）")
        return

    write_extern_user(a.extern, stocks, td_str)
    update_markdat(a.mark_dat, a.mark_bak, stocks, td_str, short_tag)


if __name__ == "__main__":
    main()

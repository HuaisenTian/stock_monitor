import json
import os
import smtplib
import time
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# 原有股价监控列表。above 表示高于阈值触发，below 表示低于阈值触发。
STOCKS = [
    {"code": "sz000975", "name": "山金国际", "target": 20.0, "condition": "below"},
    {"code": "sz000975", "name": "山金国际", "target": 30.0, "condition": "above"},
    {"code": "sz159530", "name": "机器人ETF", "target": 1.20, "condition": "below"},
    {"code": "sh603259", "name": "药明康德", "target": 150.0, "condition": "below"},
    {"code": "sz001270", "name": "铖昌科技", "target": 75.0, "condition": "below"},
    {"code": "sh603986", "name": "兆易创新", "target": 300.0, "condition": "below"},
]

# 这三只银行均在上交所上市。
BANK_STOCKS = [
    {"code": "601398", "name": "工商银行"},
    {"code": "601939", "name": "建设银行"},
    {"code": "601838", "name": "成都银行"},
]

HIGH_PRICE_SPREAD = 1.2
LOW_PRICE_SPREAD = 2.5

MAIL_HOST = "smtp.qq.com"
MAIL_USER = os.environ.get("MAIL_USER")
MAIL_PASS = os.environ.get("MAIL_PASS")
RECEIVER = os.environ.get("MAIL_RECEIVER") or MAIL_USER

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}


def http_get_text(url, params=None, headers=None, encoding="utf-8", retries=3):
    """使用标准库发送 GET 请求；失败时进行短暂重试。"""
    if params:
        url = f"{url}?{urlencode(params)}"

    request_headers = dict(DEFAULT_HEADERS)
    if headers:
        request_headers.update(headers)

    last_error = None
    for attempt in range(retries):
        try:
            request = Request(url, headers=request_headers)
            with urlopen(request, timeout=15) as response:
                return response.read().decode(encoding, errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2**attempt)

    raise RuntimeError(f"请求失败（已重试 {retries} 次）：{url}；{last_error}")


def http_get_json(url, params=None, headers=None):
    """获取并解析 JSON，避免为 GitHub Actions 增加第三方依赖。"""
    text = http_get_text(url, params=params, headers=headers)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"接口没有返回有效 JSON：{url}") from exc


def get_stock_price(stock_code):
    """从新浪行情接口获取实时股价，返回 (股票名称, 当前价格)。"""
    url = "https://hq.sinajs.cn/list=" + stock_code
    try:
        text = http_get_text(
            url,
            headers={"Referer": "https://finance.sina.com.cn"},
            encoding="gbk",
        )
        quoted = text.split('"', 2)
        if len(quoted) < 2 or not quoted[1]:
            raise ValueError("行情内容为空")
        fields = quoted[1].split(",")
        stock_name = fields[0]
        current_price = float(fields[3])
        if current_price <= 0:
            raise ValueError("当前价格无效")
        print(f"获取 {stock_name}({stock_code}) 成功，当前价格：{current_price:.3f}")
        return stock_name, current_price
    except (RuntimeError, ValueError, IndexError) as exc:
        print(f"获取股票 {stock_code} 价格失败：{exc}")
        return None, None


def get_bank_stock_price(bank):
    """复用原有新浪行情源获取银行股实时价格。"""
    name, price = get_stock_price("sh" + bank["code"])
    if price is None:
        raise RuntimeError(f"{bank['name']} 的实时价格不可用")
    return name or bank["name"], price


def get_dividend_records(stock_code):
    """获取个股最新分红记录，金额字段为每 10 股税前现金分红。"""
    payload = http_get_json(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "reportName": "RPT_SHAREBONUS_DET",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{stock_code}")',
            "pageNumber": "1",
            "pageSize": "10",
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        },
        headers={"Referer": f"https://data.eastmoney.com/yjfp/detail/{stock_code}.html"},
    )
    result = payload.get("result") or {}
    records = result.get("data") or []
    if not records:
        raise RuntimeError(f"{stock_code} 没有可用的分红记录")
    return records


def _parse_report_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def calculate_ttm_dividend_yield(records, current_price):
    """
    以最新报告期为锚点，汇总此前 12 个月（不含上年同期）的现金分红。

    东方财富 PRETAX_BONUS_RMB 的单位是“每 10 股派息人民币元”。计算中
    包含最新已公告方案，因此能及时反映尚未除息但已经公告的分红。
    """
    dated_records = []
    for record in records:
        report_date = _parse_report_date(record.get("REPORT_DATE"))
        try:
            cash_per_ten_shares = float(record.get("PRETAX_BONUS_RMB") or 0)
        except (TypeError, ValueError):
            cash_per_ten_shares = 0.0
        progress = str(record.get("ASSIGN_PROGRESS") or "")
        if report_date and cash_per_ten_shares > 0 and "取消" not in progress:
            dated_records.append((report_date, cash_per_ten_shares, progress))

    if not dated_records:
        raise RuntimeError("没有可用于计算股息率的现金分红记录")

    latest_date = max(item[0] for item in dated_records)
    cutoff = latest_date - timedelta(days=365)
    selected = [item for item in dated_records if cutoff < item[0] <= latest_date]
    annual_cash_per_share = sum(item[1] for item in selected) / 10.0
    dividend_yield = annual_cash_per_share / current_price * 100
    return dividend_yield, annual_cash_per_share, selected


def get_latest_dividend_yield(stock_code, current_price):
    """返回最新 TTM 股息率、每股现金分红合计和纳入计算的分红记录。"""
    records = get_dividend_records(stock_code)
    return calculate_ttm_dividend_yield(records, current_price)


def get_china_10y_treasury_yield():
    """获取最近一个有效交易日的中国 10 年期国债到期收益率。"""
    payload = http_get_json(
        "https://datacenter-web.eastmoney.com/api/data/get",
        params={
            "type": "RPTA_WEB_TREASURYYIELD",
            "sty": "ALL",
            "st": "SOLAR_DATE",
            "sr": "-1",
            "p": "1",
            "ps": "20",
        },
        headers={"Referer": "https://data.eastmoney.com/cjsj/zmgzsyl.html"},
    )
    rows = (payload.get("result") or {}).get("data") or []
    for row in rows:
        value = row.get("EMM00166466")
        if value not in (None, "", "-"):
            return float(value), str(row.get("SOLAR_DATE") or "")[:10]
    raise RuntimeError("没有找到有效的中国 10 年期国债收益率")


def classify_bank_spread(spread):
    """按照用户设置的严格边界判断估值状态。"""
    if spread < HIGH_PRICE_SPREAD:
        return "high"
    if spread > LOW_PRICE_SPREAD:
        return "low"
    return None


def _send_mail(subject, content):
    if not MAIL_USER or not MAIL_PASS or not RECEIVER:
        raise RuntimeError("缺少 MAIL_USER、MAIL_PASS 或 MAIL_RECEIVER 邮件配置")

    message = MIMEText(content, "plain", "utf-8")
    message["From"] = formataddr((str(Header("股票监控助手", "utf-8")), MAIL_USER))
    message["To"] = formataddr((str(Header("监控接收人", "utf-8")), RECEIVER))
    message["Subject"] = Header(subject, "utf-8")

    with smtplib.SMTP_SSL(MAIL_HOST, 465, timeout=20) as smtp_obj:
        smtp_obj.login(MAIL_USER, MAIL_PASS)
        smtp_obj.sendmail(MAIL_USER, [RECEIVER], message.as_string())
    print(f"邮件发送成功：{subject}")


def send_email(stock_name, stock_code, price, target_price, condition):
    """发送原有的股价阈值通知。"""
    if condition == "above":
        description = f"已高于目标价格 {target_price}"
    else:
        description = f"已低于目标价格 {target_price}"

    content = (
        "【股价提醒】\n"
        f"股票：{stock_name} ({stock_code})\n"
        f"当前价格：{price}\n"
        f"状态：{description}\n"
    )
    _send_mail(f"股价提醒：{stock_name} 当前价格 {price}", content)


def send_bank_spread_alerts(alerts, treasury_yield, treasury_date):
    """将本轮触发的银行股利差提醒合并成一封邮件。"""
    low_names = [item["name"] for item in alerts if item["status"] == "low"]
    high_names = [item["name"] for item in alerts if item["status"] == "high"]
    labels = []
    if high_names:
        labels.append("高位警告：" + "、".join(high_names))
    if low_names:
        labels.append("低谷提醒：" + "、".join(low_names))

    lines = [
        "【银行股股息率利差监控】",
        "口径：TTM 股息率（含最新已公告方案）- 中国10年期国债收益率",
        f"中国10年期国债收益率：{treasury_yield:.4f}%（{treasury_date}）",
        f"阈值：利差 < {HIGH_PRICE_SPREAD:.1f} 个百分点为高位；"
        f"利差 > {LOW_PRICE_SPREAD:.1f} 个百分点为低谷",
        "",
    ]
    for item in alerts:
        state = "达到高位警戒区" if item["status"] == "high" else "达到低谷关注区"
        periods = "、".join(str(record[0]) for record in item["dividend_records"])
        lines.extend(
            [
                f"{item['name']} ({item['code']})：{state}",
                f"  当前股价：{item['price']:.2f} 元",
                f"  TTM每股现金分红：{item['cash_per_share']:.4f} 元",
                f"  TTM股息率：{item['dividend_yield']:.4f}%",
                f"  股债利差：{item['spread']:.4f} 个百分点",
                f"  纳入分红报告期：{periods}",
                "",
            ]
        )

    _send_mail("[银行股监控] " + "；".join(labels), "\n".join(lines))


def run_bank_yield_monitor():
    """监控三只银行股的 TTM 股息率相对 10 年期国债收益率的利差。"""
    treasury_yield, treasury_date = get_china_10y_treasury_yield()
    print(
        f"中国10年期国债收益率：{treasury_yield:.4f}% "
        f"（数据日期：{treasury_date}）"
    )

    results = []
    alerts = []
    for bank in BANK_STOCKS:
        try:
            name, price = get_bank_stock_price(bank)
            dividend_yield, cash_per_share, dividend_records = (
                get_latest_dividend_yield(bank["code"], price)
            )
            spread = dividend_yield - treasury_yield
            status = classify_bank_spread(spread)
            result = {
                "name": name,
                "code": bank["code"],
                "price": price,
                "dividend_yield": dividend_yield,
                "cash_per_share": cash_per_share,
                "dividend_records": dividend_records,
                "treasury_yield": treasury_yield,
                "treasury_date": treasury_date,
                "spread": spread,
                "status": status,
            }
            results.append(result)
            print(
                f"{name}({bank['code']})：股价 {price:.2f} 元，"
                f"TTM股息率 {dividend_yield:.4f}%，股债利差 {spread:.4f} 个百分点"
            )
            if status:
                alerts.append(result)
        except (RuntimeError, TypeError, ValueError) as exc:
            print(f"监控 {bank['name']}({bank['code']}) 失败：{exc}")

    if alerts:
        send_bank_spread_alerts(alerts, treasury_yield, treasury_date)
    else:
        print("银行股股债利差均未触发提醒。")
    return results


def run_price_monitor():
    """运行原有的固定股价阈值监控。"""
    for stock in STOCKS:
        name, price = get_stock_price(stock["code"])
        if price is None:
            continue

        target = stock["target"]
        condition = stock["condition"]
        triggered = (condition == "above" and price >= target) or (
            condition == "below" and price <= target
        )
        if triggered:
            print(
                f"股票 {stock['name']} 触发条件（{condition} {target}），"
                f"当前价格 {price}，正在发送邮件..."
            )
            send_email(name, stock["code"], price, target, condition)
        else:
            print(
                f"股票 {stock['name']} 未触发（当前 {price}，"
                f"条件 {condition} {target}）"
            )


def main():
    if not MAIL_USER or not MAIL_PASS or not RECEIVER:
        raise SystemExit(
            "错误：请在 GitHub Secrets 中配置 MAIL_USER、MAIL_PASS；"
            "可选配置 MAIL_RECEIVER（默认与 MAIL_USER 相同）。"
        )

    run_bank_yield_monitor()
    run_price_monitor()


if __name__ == "__main__":
    main()

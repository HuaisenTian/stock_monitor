import requests
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
import os

# 股票监控列表：代码、名称、触发价格、触发条件（above 表示高于阈值触发，below 表示低于阈值触发）
STOCKS = [
    {"code": "sz000975", "name": "山金国际", "target": 19.0, "condition": "below"},
    {"code": "sz000975", "name": "山金国际", "target": 35.0, "condition": "above"},
    {"code": "sz159530", "name": "机器人ETF", "target": 1.8, "condition": "above"}
]

MAIL_HOST = "smtp.qq.com"
MAIL_USER = os.environ.get("MAIL_USER")
MAIL_PASS = os.environ.get("MAIL_PASS")
RECEIVER = os.environ.get("MAIL_USER")


def get_stock_price(stock_code):
    """根据股票代码获取实时股价，返回 (股票名称, 当前价格) 或 (None, None)"""
    url = f"http://hq.sinajs.cn/list={stock_code}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.text.split('"')[1].split(',')
            stock_name = data[0]
            current_price = float(data[3])
            print(f"获取 {stock_name}({stock_code}) 成功，当前价格: {current_price}")
            return stock_name, current_price
    except Exception as e:
        print(f"获取股票 {stock_code} 价格失败: {e}")
        return None, None
    return None, None


def send_email(stock_name, stock_code, price, target_price, condition):
    """发送邮件通知，condition 为 'above' 或 'below'"""
    if condition == "above":
        action = "超过"
        description = f"已超过目标价格 {target_price}"
    else:
        action = "低于"
        description = f"已低于目标价格 {target_price}"

    content = f"【股价提醒】\n股票：{stock_name} ({stock_code})\n当前价格：{price}\n{description}。"

    message = MIMEText(content, 'plain', 'utf-8')
    message['From'] = formataddr(["股价监控助手", MAIL_USER])
    message['To'] = formataddr(["我自己", RECEIVER])
    message['Subject'] = Header(f"注意！{stock_name} 股价已达 {price}", 'utf-8')

    try:
        smtp_obj = smtplib.SMTP_SSL(MAIL_HOST, 465)
        print("连接SMTP服务器成功...")
        smtp_obj.login(MAIL_USER, MAIL_PASS)
        print("登录成功...")
        smtp_obj.sendmail(MAIL_USER, [RECEIVER], message.as_string())
        print("邮件发送成功")
        smtp_obj.quit()
    except smtplib.SMTPException as e:
        print(f"邮件发送失败: {e}")


if __name__ == "__main__":
    if not MAIL_USER or not MAIL_PASS:
        print("错误：未检测到环境变量，请在Github Secrets中配置。")
        exit(1)

    for stock in STOCKS:
        name, price = get_stock_price(stock["code"])
        if price is None:
            continue  # 获取失败，跳过该股票

        target = stock["target"]
        condition = stock["condition"]

        # 根据触发条件判断
        if (condition == "above" and price >= target) or (condition == "below" and price <= target):
            print(f"股票 {stock['name']} 触发条件（{condition} {target}），当前价格 {price}，正在发送邮件...")
            send_email(name, stock["code"], price, target, condition)
        else:
            print(f"股票 {stock['name']} 未触发（当前 {price}，条件 {condition} {target}）")
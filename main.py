import requests
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
import os

STOCK_CODE = "sh600795"  # 国电电力 (上海证券交易所)
TARGET_PRICE = 4.7       # 触发价格
MAIL_HOST = "smtp.qq.com"
MAIL_USER = os.environ.get("MAIL_USER")
MAIL_PASS = os.environ.get("MAIL_PASS")
RECEIVER = os.environ.get("MAIL_USER")


def get_stock_price():
    url = f"http://hq.sinajs.cn/list={STOCK_CODE}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.text.split('"')[1].split(',')
            stock_name = data[0]
            current_price = float(data[3])
            print(f"当前 {stock_name} 价格: {current_price}")
            return stock_name, current_price
    except Exception as e:
        print(f"获取股价失败: {e}")
        return None, None
    return None, None

def send_email(stock_name, price):
    content = f"【股价提醒】\n股票：{stock_name} ({STOCK_CODE})\n当前价格：{price}\n已超过目标价格 {TARGET_PRICE}。"

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

    name, price = get_stock_price()

    if price is not None:
        if price > TARGET_PRICE:
            print(f"价格 {price} > {TARGET_PRICE}，正在发送邮件...")
            send_email(name, price)
        else:
            print(f"价格 {price} <= {TARGET_PRICE}，无需发送邮件。")

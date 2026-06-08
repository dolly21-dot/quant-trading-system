"""
通知系统 - 多通道推送
支持: 控制台、文件日志、邮件、Webhook(企业微信/钉钉/Slack/Discord/Telegram)
所有通知分级: INFO / WARNING / CRITICAL
"""
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pathlib import Path

import httpx
from loguru import logger

from config.settings import BASE_DIR


class NotificationLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationType(Enum):
    TRADE_SIGNAL = "trade_signal"       # 交易信号
    ORDER_EXECUTED = "order_executed"   # 订单执行
    RISK_ALERT = "risk_alert"           # 风控预警
    DAILY_REVIEW = "daily_review"       # 每日复盘
    WEEKLY_REVIEW = "weekly_review"     # 周度复盘
    SYSTEM_STATUS = "system_status"     # 系统状态
    ERROR = "error"                     # 系统错误


class NotificationMessage:
    """通知消息"""

    def __init__(self, title: str, body: str, level: NotificationLevel = NotificationLevel.INFO,
                 notification_type: NotificationType = NotificationType.SYSTEM_STATUS,
                 data: dict = None):
        self.title = title
        self.body = body
        self.level = level
        self.notification_type = notification_type
        self.data = data or {}
        self.timestamp = datetime.now(timezone.utc)

    @property
    def emoji(self) -> str:
        emoji_map = {
            NotificationLevel.INFO: "ℹ️",
            NotificationLevel.WARNING: "⚠️",
            NotificationLevel.CRITICAL: "🚨",
        }
        type_emoji = {
            NotificationType.TRADE_SIGNAL: "📊",
            NotificationType.ORDER_EXECUTED: "✅",
            NotificationType.RISK_ALERT: "⛔",
            NotificationType.DAILY_REVIEW: "📋",
            NotificationType.WEEKLY_REVIEW: "📈",
            NotificationType.SYSTEM_STATUS: "🖥️",
            NotificationType.ERROR: "❌",
        }
        return f"{type_emoji.get(self.notification_type, '')} {emoji_map.get(self.level, '')}"

    def to_text(self) -> str:
        return f"{self.emoji} [{self.level.value.upper()}] {self.title}\n{self.body}"

    def to_markdown(self) -> str:
        return f"### {self.emoji} {self.title}\n\n**级别**: {self.level.value.upper()} | **时间**: {self.timestamp.strftime('%Y-%m-%d %H:%M UTC')}\n\n{self.body}"


class NotificationChannel:
    """通知通道基类"""

    def send(self, message: NotificationMessage) -> bool:
        raise NotImplementedError


class ConsoleChannel(NotificationChannel):
    """控制台通道"""

    def send(self, message: NotificationMessage) -> bool:
        print(f"\n{'='*60}")
        print(message.to_text())
        print(f"{'='*60}\n")
        return True


class FileChannel(NotificationChannel):
    """文件通道 - 写入通知日志"""

    def __init__(self, log_dir: str = None):
        self.log_dir = Path(log_dir or BASE_DIR / "logs" / "notifications")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def send(self, message: NotificationMessage) -> bool:
        date_str = message.timestamp.strftime("%Y-%m-%d")
        log_file = self.log_dir / f"notify_{date_str}.log"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{message.timestamp.isoformat()}] {message.to_text()}\n\n")
        return True


class WebhookChannel(NotificationChannel):
    """
    通用 Webhook 通道
    支持: 企业微信、钉钉、Slack、Discord、Telegram
    """

    def __init__(self, webhook_url: str = None, channel_type: str = "generic"):
        self.webhook_url = webhook_url
        self.channel_type = channel_type
        self._client = httpx.Client(timeout=15.0)

    def send(self, message: NotificationMessage) -> bool:
        if not self.webhook_url:
            logger.debug("Webhook URL未配置，跳过推送")
            return False

        try:
            payload = self._build_payload(message)
            headers = {"Content-Type": "application/json"}

            response = self._client.post(
                self.webhook_url, json=payload, headers=headers
            )
            response.raise_for_status()
            logger.debug(f"Webhook推送成功: {self.channel_type}")
            return True

        except Exception as e:
            logger.warning(f"Webhook推送失败 ({self.channel_type}): {e}")
            return False

    def _build_payload(self, message: NotificationMessage) -> dict:
        if self.channel_type == "wechat":
            return self._build_wechat_payload(message)
        elif self.channel_type == "dingtalk":
            return self._build_dingtalk_payload(message)
        elif self.channel_type == "slack":
            return self._build_slack_payload(message)
        elif self.channel_type == "discord":
            return self._build_discord_payload(message)
        elif self.channel_type == "telegram":
            return self._build_telegram_payload(message)
        else:
            return {"title": message.title, "body": message.body, "level": message.level.value}

    def _build_wechat_payload(self, msg: NotificationMessage) -> dict:
        """企业微信格式"""
        return {
            "msgtype": "markdown",
            "markdown": {
                "content": f"### {msg.emoji} {msg.title}\n> 级别: {msg.level.value}\n> 时间: {msg.timestamp.strftime('%Y-%m-%d %H:%M')}\n\n{msg.body}"
            }
        }

    def _build_dingtalk_payload(self, msg: NotificationMessage) -> dict:
        """钉钉格式"""
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": f"{msg.emoji} {msg.title}",
                "text": f"### {msg.emoji} {msg.title}\n\n**级别**: {msg.level.value.upper()}\n\n{msg.body}"
            }
        }

    def _build_slack_payload(self, msg: NotificationMessage) -> dict:
        """Slack格式"""
        color_map = {
            NotificationLevel.INFO: "#36a64f",
            NotificationLevel.WARNING: "#ff9800",
            NotificationLevel.CRITICAL: "#ff0000",
        }
        return {
            "attachments": [{
                "color": color_map.get(msg.level, "#cccccc"),
                "title": f"{msg.emoji} {msg.title}",
                "text": msg.body,
                "ts": int(msg.timestamp.timestamp()),
                "fields": [
                    {"title": "Level", "value": msg.level.value.upper(), "short": True},
                    {"title": "Type", "value": msg.notification_type.value, "short": True},
                ]
            }]
        }

    def _build_discord_payload(self, msg: NotificationMessage) -> dict:
        """Discord格式"""
        color_map = {
            NotificationLevel.INFO: 3066993,    # green
            NotificationLevel.WARNING: 15105570, # orange
            NotificationLevel.CRITICAL: 15158332, # red
        }
        return {
            "embeds": [{
                "title": f"{msg.emoji} {msg.title}",
                "description": msg.body[:2048],
                "color": color_map.get(msg.level, 0),
                "timestamp": msg.timestamp.isoformat(),
                "fields": [
                    {"name": "Level", "value": msg.level.value.upper(), "inline": True},
                    {"name": "Type", "value": msg.notification_type.value, "inline": True},
                ]
            }]
        }

    def _build_telegram_payload(self, msg: NotificationMessage) -> dict:
        """Telegram格式 (需通过Bot API)"""
        return {
            "text": msg.to_text(),
            "parse_mode": "HTML",
        }


class EmailChannel(NotificationChannel):
    """邮件通道"""

    def __init__(self, smtp_host: str = "smtp.gmail.com", smtp_port: int = 587,
                 sender: str = None, password: str = None, recipients: list = None):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.password = password
        self.recipients = recipients or []

    def send(self, message: NotificationMessage) -> bool:
        if not self.sender or not self.recipients:
            logger.debug("邮件配置不完整，跳过")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[QuantSystem] {message.emoji} {message.title}"
            msg["From"] = self.sender
            msg["To"] = ", ".join(self.recipients)

            text_part = MIMEText(message.to_text(), "plain", "utf-8")
            html_part = MIMEText(message.to_markdown(), "html", "utf-8")
            msg.attach(text_part)
            msg.attach(html_part)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                if self.password:
                    server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipients, msg.as_string())

            logger.debug(f"邮件发送成功: {message.title}")
            return True

        except Exception as e:
            logger.warning(f"邮件发送失败: {e}")
            return False


class NotificationManager:
    """
    通知管理器 - 统一管理所有通知通道
    根据消息级别自动选择通道:
      - INFO → 控制台 + 文件
      - WARNING → 控制台 + 文件 + Webhook
      - CRITICAL → 控制台 + 文件 + Webhook + 邮件
    """

    def __init__(self):
        self.channels: list[NotificationChannel] = []
        self.history: list[dict] = []
        self._init_default_channels()

    def _init_default_channels(self):
        """初始化默认通道"""
        # 控制台始终开启
        self.channels.append(ConsoleChannel())
        # 文件日志始终开启
        self.channels.append(FileChannel())

    def add_webhook(self, url: str, channel_type: str = "generic"):
        """添加Webhook通道"""
        self.channels.append(WebhookChannel(url, channel_type))
        logger.info(f"通知通道已添加: Webhook ({channel_type})")

    def add_email(self, smtp_host: str, smtp_port: int, sender: str,
                  password: str, recipients: list):
        """添加邮件通道"""
        self.channels.append(EmailChannel(smtp_host, smtp_port, sender, password, recipients))
        logger.info(f"通知通道已添加: Email ({sender} -> {recipients})")

    def notify(self, title: str, body: str,
               level: NotificationLevel = NotificationLevel.INFO,
               notification_type: NotificationType = NotificationType.SYSTEM_STATUS,
               data: dict = None):
        """发送通知"""
        message = NotificationMessage(title, body, level, notification_type, data)

        # 记录历史
        self.history.append({
            "timestamp": message.timestamp.isoformat(),
            "title": title,
            "level": level.value,
            "type": notification_type.value,
        })

        # 只保留最近500条
        if len(self.history) > 500:
            self.history = self.history[-500:]

        # 根据级别选择通道
        for channel in self.channels:
            try:
                if isinstance(channel, ConsoleChannel):
                    channel.send(message)
                elif isinstance(channel, FileChannel):
                    channel.send(message)
                elif isinstance(channel, WebhookChannel):
                    # WARNING及以上才发Webhook
                    if level in (NotificationLevel.WARNING, NotificationLevel.CRITICAL):
                        channel.send(message)
                elif isinstance(channel, EmailChannel):
                    # CRITICAL才发邮件
                    if level == NotificationLevel.CRITICAL:
                        channel.send(message)
            except Exception as e:
                logger.error(f"通知通道异常: {e}")

    # ============================================================
    # 便捷方法 - 各场景预设通知
    # ============================================================

    def notify_trade_signal(self, symbol: str, strategy: str,
                            signal_type: str, strength: float, price: float,
                            stop_loss: float = None, take_profit: float = None,
                            reason: str = ""):
        """交易信号通知"""
        body = (
            f"**股票**: {symbol}\n"
            f"**策略**: {strategy}\n"
            f"**方向**: {signal_type}\n"
            f"**强度**: {strength:.2f}\n"
            f"**价格**: ${price:.2f}\n"
            f"**止损**: ${stop_loss:.2f}" if stop_loss else "" + "\n"
            f"**止盈**: ${take_profit:.2f}" if take_profit else "" + "\n"
            f"**原因**: {reason}"
        )
        level = NotificationLevel.INFO if strength < 0.7 else NotificationLevel.WARNING
        self.notify(
            title=f"交易信号: {signal_type} {symbol}",
            body=body,
            level=level,
            notification_type=NotificationType.TRADE_SIGNAL,
        )

    def notify_order_executed(self, symbol: str, side: str, quantity: float,
                               price: float, order_id: str = ""):
        """订单执行通知"""
        total = quantity * price
        self.notify(
            title=f"订单执行: {side} {symbol}",
            body=f"**方向**: {side}\n**数量**: {quantity}\n**价格**: ${price:.2f}\n**总额**: ${total:,.2f}\n**订单号**: {order_id}",
            level=NotificationLevel.INFO,
            notification_type=NotificationType.ORDER_EXECUTED,
        )

    def notify_risk_alert(self, alert_type: str, details: str):
        """风控预警通知"""
        self.notify(
            title=f"风控预警: {alert_type}",
            body=details,
            level=NotificationLevel.CRITICAL,
            notification_type=NotificationType.RISK_ALERT,
        )

    def notify_daily_review(self, summary: str, return_pct: float,
                            sharpe: float, win_rate: float):
        """每日复盘通知"""
        level = NotificationLevel.INFO if return_pct >= 0 else NotificationLevel.WARNING
        self.notify(
            title=f"每日复盘: 收益{'+' if return_pct >= 0 else ''}{return_pct:.2f}%",
            body=summary,
            level=level,
            notification_type=NotificationType.DAILY_REVIEW,
        )

    def notify_system_error(self, error_msg: str, module: str = ""):
        """系统错误通知"""
        self.notify(
            title=f"系统错误: {module}",
            body=error_msg,
            level=NotificationLevel.CRITICAL,
            notification_type=NotificationType.ERROR,
        )

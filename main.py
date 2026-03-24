import json
import asyncio
from aiohttp import web
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

@register("astrbot_plugin_order_approval", "AstrBot助手", "通过 Webhook 接收订单信息并发送至指定会话进行审批", "1.0.2", "https://github.com/yourname/astrbot_plugin_order_approval")
class OrderApprovalPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._server_task = None
        self._runner = None
        
        # 用于追踪待审批的订单：{target_id: {order_data}}
        # 在实际复杂场景下，建议使用 message_id 映射，但此处采用简化的会话锁定逻辑
        self.pending_orders = {} 

        # 启动 Webhook 服务
        asyncio.create_task(self.start_webhook_server())

    def _build_session_candidates(self, raw_target: str):
        """将旧格式会话 ID（QQ:123 / GROUP:456）兼容转换为 AstrBot 统一会话 ID。"""
        if not isinstance(raw_target, str):
            return []

        target = raw_target.strip()
        if not target:
            return []

        # 已是统一会话 ID：adapter:session_type:session_id
        if target.count(":") >= 2:
            return [target]

        # 纯数字时，按 QQ 私聊 ID 自动兼容
        if target.isdigit():
            return [
                f"aiocqhttp:FriendMessage:{target}",
                f"onebot:FriendMessage:{target}",
                f"qq:friend:{target}",
                f"qq:private:{target}",
            ]

        parts = target.split(":", 1)
        if len(parts) != 2:
            return []

        prefix, sid = parts[0].upper(), parts[1].strip()
        if not sid:
            return [target]

        # 常见 AstrBot/OneBot 适配器会话类型的兼容候选
        if prefix == "QQ":
            return [
                f"aiocqhttp:FriendMessage:{sid}",
                f"onebot:FriendMessage:{sid}",
                f"qq:friend:{sid}",
                f"qq:private:{sid}",
            ]
        if prefix == "GROUP":
            return [
                f"aiocqhttp:GroupMessage:{sid}",
                f"onebot:GroupMessage:{sid}",
                f"qq:group:{sid}",
            ]

        return []

    def _get_vendor_mapping(self):
        """读取供应商映射，仅支持 ["user_number:vendor_id"] 格式。"""
        approval_cfg = self.config.get("approval_logic", {})
        vendor_mapping_raw = approval_cfg.get("vendor_mapping", "")
        mapping = {}

        if not isinstance(vendor_mapping_raw, str) or not vendor_mapping_raw.strip():
            return mapping

        try:
            mapping_rows = json.loads(vendor_mapping_raw)
        except Exception:
            logger.warning("vendor_mapping 配置无效：必须为 JSON 数组，元素格式为 user_number:vendor_id。")
            return mapping

        if not isinstance(mapping_rows, list):
            logger.warning("vendor_mapping 配置无效：必须为 JSON 数组。")
            return mapping

        for row in mapping_rows:
            if not isinstance(row, str):
                continue
            item = row.strip()
            if ":" not in item:
                continue
            user_number, vendor_id = item.split(":", 1)
            user_number = user_number.strip()
            vendor_id = vendor_id.strip()
            if user_number and vendor_id:
                mapping[vendor_id] = f"QQ:{user_number}"

        return mapping

    async def _send_message_compat(self, raw_target: str, chain: MessageChain) -> str:
        """向目标会话发送消息，兼容旧配置格式，返回实际发送成功的统一会话 ID。"""
        candidates = self._build_session_candidates(raw_target)
        last_error = None
        for session_id in candidates:
            try:
                await self.context.send_message(session_id, chain)
                return session_id
            except Exception as e:
                last_error = e
                logger.warning(f"发送到会话 {session_id} 失败，尝试下一个候选: {e}")

        if last_error:
            raise last_error
        raise ValueError("目标会话 ID 为空")

    async def start_webhook_server(self):
        """启动 aiohttp Webhook 服务器"""
        webhook_cfg = self.config.get("webhook_settings", {})
        port = webhook_cfg.get("port", 8080)
        path = webhook_cfg.get("path", "/webhook/order")

        app = web.Application()
        app.router.add_post(path, self.handle_webhook_request)
        
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '0.0.0.0', port)
        
        try:
            await site.start()
            logger.info(f"订单审批 Webhook 服务已启动: http://0.0.0.0:{port}{path}")
        except Exception as e:
            logger.error(f"Webhook 服务启动失败: {e}")

    async def handle_webhook_request(self, request):
        """处理外部系统推送的 Webhook 请求"""
        try:
            # 安全校验
            security_token = self.config.get("webhook_settings", {}).get("security_token", "")
            if security_token:
                auth_header = request.headers.get("X-Token") or request.headers.get("Authorization")
                if auth_header != security_token:
                    return web.Response(status=403, text="Invalid Token")

            data = await request.json()
            order_id = data.get("order_id", "未知")
            name = data.get("name", "未命名订单")
            vendor = data.get("vendor", "未知供应商")
            total_amount = data.get("total_amount", 0)
            currency = data.get("currency", "CNY")
            url = data.get("url", "#")

            # 匹配审批人
            approval_cfg = self.config.get("approval_logic", {})
            vendor_mapping = self._get_vendor_mapping()
            target_id = vendor_mapping.get(vendor) or approval_cfg.get("default_target")

            if not target_id:
                logger.warning(f"订单 {name} 无法找到对应的审批人，已忽略。")
                return web.json_response({"status": "ignored", "reason": "no_target"})

            # 构建消息
            tmpl_cfg = self.config.get("message_template", {})
            card_text = tmpl_cfg.get("approval_card", "").format(
                name=name,
                vendor=vendor,
                total_amount=total_amount,
                currency=currency,
                url=url
            )

            # 发送审批请求
            chain = MessageChain().message(card_text)
            resolved_target_id = await self._send_message_compat(target_id, chain)

            # 记录待审批状态
            self.pending_orders[resolved_target_id] = {
                "order_id": order_id,
                "name": name,
                "vendor": vendor,
                "url": url
            }

            return web.json_response({"status": "success", "order_id": order_id})
        except Exception as e:
            logger.error(f"处理 Webhook 数据时出错: {e}")
            return web.Response(status=500, text=str(e))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_approval_reply(self, event: AstrMessageEvent):
        """监听审批人的回复内容"""
        # 获取当前会话 ID (统一 ID 格式)
        current_uid = event.unified_msg_origin
        
        # 检查该会话是否有待处理订单
        if current_uid not in self.pending_orders:
            return

        msg_str = event.message_str.strip()
        kw_cfg = self.config.get("keywords", {})
        approve_text = kw_cfg.get("approve_text", "同意")
        reject_text = kw_cfg.get("reject_text", "不同意")

        order_info = self.pending_orders[current_uid]
        status = ""

        if msg_str == approve_text:
            status = "已通过"
        elif msg_str == reject_text:
            status = "已拒绝"
        else:
            # 不是审批关键字，不处理，继续等待或让其他插件处理
            return

        # 1. 反馈给审批人
        yield event.plain_result(f"订单 {order_info['name']} 审批处理完成：{status}")

        # 2. 通知管理员
        admin_id = self.config.get("approval_logic", {}).get("admin_id")
        if admin_id:
            report_tmpl = self.config.get("message_template", {}).get("report_template", "")
            report_msg = report_tmpl.format(
                status=status,
                name=order_info['name'],
                url=order_info['url']
            )
            report_chain = MessageChain().message(report_msg)
            try:
                await self._send_message_compat(admin_id, report_chain)
            except Exception as e:
                logger.warning(f"转发给 admin {admin_id} 失败：{e}")

        # 3. 移除待处理状态
        del self.pending_orders[current_uid]
        
        # 停止事件传播，防止其他插件误触发
        event.stop_event()

    @filter.command("order_config")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def order_config(self, event: AstrMessageEvent):
        """查看当前的订单通知映射配置"""
        approval_cfg = self.config.get("approval_logic", {})
        mapping = self._get_vendor_mapping()
        default = approval_cfg.get("default_target", "未设置")
        admin = approval_cfg.get("admin_id", "未设置")
        
        res = "【订单审批配置预览】\n"
        res += f"默认审批会话: {default}\n"
        res += f"结果通知管理员: {admin}\n"
        res += "供应商映射表:\n"
        for vendor, target in mapping.items():
            res += f"- {vendor} => {target}\n"
            
        yield event.plain_result(res.strip())

    async def terminate(self):
        """插件卸载时关闭服务器"""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Webhook 服务已关闭")
        super().terminate()

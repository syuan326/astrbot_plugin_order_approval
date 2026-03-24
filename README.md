markdown
# astrbot_plugin_order_approval

通过 Webhook 接收外部系统的订单信息，自动推送至指定的群聊或私聊会话进行审批。审批人员可以通过引用消息并回复“同意”或“不同意”来完成操作，系统随后会将审批结果自动汇报给管理员。

## 插件简介

本插件旨在简化企业或团队的订单审核流程。通过对接外部系统的 Webhook（如 Odoo、ERP 等），将繁杂的订单信息转化为即时通讯软件中的结构化消息。结合 AstrBot 的会话控制功能，实现“接收-通知-审批-反馈”的全自动化闭环管理。

## 功能说明

- **Webhook 接收**：支持接收包含订单号、供应商、总金额、货币类型及详情链接的 JSON 数据。
- **动态供应商映射**：根据配置好的 `vendor:QQ号` 映射关系，自动定位负责该供应商的审批人员。
- **结构化审批消息**：自动生成易读的订单卡片，包含所有关键订单要素。
- **交互式审批**：审批人通过回复“同意”或“不同意”即可完成审批，无需登录外部系统。
- **管理员同步**：无论审批结果如何，系统都会将最终状态及单号信息汇总发送给配置的管理员。

## 插件流程

1. **数据触发**：外部系统向插件预设的 Webhook 接口发送 POST 请求，携带订单 JSON 数据。
2. **逻辑解析**：
   - 插件解析 JSON 字段（`order_id`, `name`, `vendor`, `total_amount` 等）。
   - 匹配配置项中的 `vendor_mapping`。如果匹配到特定的供应商映射，则将消息发送至对应的 QQ/群聊；若未匹配，则发送至默认配置的审批会话。
3. **消息推送**：构造格式化消息并发送，同时为该条消息启动 **会话控制器 (SessionController)**。
4. **会话等待**：插件进入等待状态，监听指定会话中引用该订单消息的回复。
5. **审批动作判断**：
   - 若用户回复“同意”：记录审批通过状态。
   - 若用户回复“不同意”：记录审批拒绝状态。
6. **结果汇报**：
   - 向审批会话反馈“审批已完成”。
   - 调用 `self.context.send_message` 向管理员账号发送最终汇总信息（包含单号与详情链接）。
7. **会话结束**：关闭当前订单的监听，释放资源。

## 使用方法

### 1. 发送 Webhook 数据
确保您的外部系统向 AstrBot 监听的 Webhook 地址发送如下结构的 JSON：
```json
{
  "order_id": "SO-2026-001",
  "name": "办公椅采购单",
  "vendor": "Azure Interior",
  "total_amount": 1999.00,
  "currency": "CNY",
  "url": "https://example.com/orders/SO-2026-001"
}
```

### 2. 会话 ID 配置说明

支持以下几种配置方式：

- 三段式统一会话 ID（推荐）
  - `aiocqhttp:FriendMessage:12345678`
  - `aiocqhttp:GroupMessage:987654321`
- 兼容旧格式
  - `QQ:12345678`
  - `GROUP:987654321`
- 纯数字 QQ 号（会自动按私聊候选尝试）
  - `12345678`

### 3. 供应商映射配置

`approval_logic.vendor_mapping` 推荐使用以下格式（支持私聊或群聊会话）：

```json
[
  {"Pardofelis:FriendMessage:2331329306": "Azure Interior"},
  {"Pardofelis:GroupMessage:987654321": "Acme Corp"}
]
```

其中：
- key 为 AstrBot 统一会话 ID（例如 `Pardofelis:FriendMessage:2331329306` 或 `Pardofelis:GroupMessage:987654321`）；
- value 为 webhook 数据里的 `vendor` 字段值。

> 兼容旧格式：`["12345678:Azure Interior"]`（会自动按 QQ 私聊候选会话尝试发送）。

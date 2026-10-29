# 跨领域迁移：商业关系分析

恋爱分析和商业关系分析**是同一个引擎换词表**。你已有的「微信群导出 → JSON → SQL → AI 分析」管道不用重写，只换画像层与策略层的语义。

---

## 概念映射

| 恋爱/人际 | 供应链 / 客户 | 职场 / 合作方 |
|---|---|---|
| 主动发起会话 | 主动询盘 / 主动报价 / 主动同步进度 | 主动同步 / 主动求助 |
| 回应率 | 询价响应率 | 消息响应率 |
| 回复延迟中位数 | **报价响应速度** | 响应时效 |
| 句均长度 / 投入量 | 沟通详尽度 / 让价意愿 | 信息完整度 |
| 敷衍率 | 敷衍报价 / 模板回复 | 应付式回复 |
| 提问率 | 需求澄清深度 | 追问细节的意愿 |
| 深夜占比 | 紧急响应意愿 | 加班配合度 |
| 最长连发 | 单方催促（催货/催款） | 单方施压 |
| 动量下滑 | **合作热度下降**（流失前兆） | 协作降频 |
| 情绪正负比 | 抱怨度 / 满意度 | 摩擦程度 |
| 承诺维度 | 长约意愿 / 独家 / 备货承诺 | 长期合作倾向 |
| 追逃循环 | 催货—拖延循环 | 追进度—回避循环 |

**核心洞察不变**：不对称性、回应质量、动量趋势，这三样在任何关系里都是最硬的指标。

---

## 供应商画像卡（模板）

```markdown
### 供应商画像：{工厂/公司}

| 维度 | 判断 | 证据 |
|---|---|---|
| 响应速度 | 快（中位 {X} 分钟） | 近 {N} 次询价 |
| 报价积极度 | 高/中/低 | 主动报价 {N} 次 / 被询价 {M} 次 |
| 议价空间 | {幅度} | 历史让价记录 |
| 交付稳定性 | {准时率} | 近 {N} 单 |
| 沟通详尽度 | 高/中/低 | 句均长度、是否主动澄清需求 |
| 问题响应 | 快/慢 | 异常事件的处理时效 |
| 抱怨度 | {正负比} | 负向表达占比 |
| 关系定位 | 战略供应商 / 主力 / 备选 / 淘汰候选 | 综合 |
| 下一步 | {增加询价频率 / 谈长约 / 引入备选 / 减少依赖} | — |
```

---

## 客户画像卡（模板）

```markdown
### 客户画像：{公司/联系人}

| 维度 | 判断 |
|---|---|
| 询盘频率与动量 | ↑ / → / ↓（流失前兆看这里） |
| 询盘深度 | 比价型 / 需求明确型 / 探索型 |
| 价格敏感度 | 高/中/低 |
| 决策角色 | 决策人 / 影响者 / 执行层 |
| 响应习惯 | 活跃时段、回复规律 |
| 关系温度 | 交易型 / 伙伴型 |
| 风险信号 | 长期不回应 / 只问价不下单 / 引入竞品对比 |
| 下一步 | {触达节奏 / 给什么信息 / 何时推进} |
```

---

## 迁移步骤

1. **复用数据层**：用户导入微信群导出的记录后用 `parse_chat.py` 直接跑（JSON 最佳）。`--gap` 调到 480–1440（商务沟通间隔大）。
2. **换词表**：`scripts/parse_chat.py` 里的情绪词典替换为业务词典（报价词、交付词、抱怨词、承诺词）。
3. **改画像维度**：用上面的画像卡替换 12 维人际画像。
4. **改策略层**：把「7 天行动」换成「商务动作」（是否涨价、是否给账期、是否引入备选、何时催款）。
5. **删掉不适用项**：依恋模型、Sternberg 三角在商业场景**不适用**，保留 Gottman 的正负比与「投标转向率」（对应：对方抛出的小需求/小问题，你有没有接住）。

---

## 建议的落库结构（微信群 → JSON → SQL）

```sql
-- 原始消息
CREATE TABLE chat_message (
  id           BIGINT PRIMARY KEY,
  room_id      TEXT NOT NULL,      -- 群/会话标识
  sender       TEXT NOT NULL,      -- 说话人（需要脱敏就在这一层做）
  sent_at      TIMESTAMP NOT NULL,
  content      TEXT,
  msg_type     TEXT,               -- text/image/voice/system
  raw          JSONB               -- 原始记录，便于回溯
);
CREATE INDEX idx_msg_room_time ON chat_message(room_id, sent_at);
CREATE INDEX idx_msg_sender    ON chat_message(sender, sent_at);

-- 按「关系对」+ 时间窗聚合出的指标快照
CREATE TABLE relation_metrics (
  id           BIGINT PRIMARY KEY,
  room_id      TEXT NOT NULL,
  party_a      TEXT NOT NULL,
  party_b      TEXT NOT NULL,
  window_start DATE NOT NULL,
  window_end   DATE NOT NULL,
  metrics      JSONB NOT NULL,     -- parse_chat.py 的输出原样存
  signals      JSONB,              -- 检测到的信号数组
  created_at   TIMESTAMP DEFAULT now(),
  UNIQUE (room_id, party_a, party_b, window_start, window_end)
);

-- 画像（会随新数据更新，保留历史版本）
CREATE TABLE party_profile (
  id         BIGINT PRIMARY KEY,
  room_id    TEXT NOT NULL,
  party      TEXT NOT NULL,
  profile    JSONB NOT NULL,       -- 画像卡结构化
  version    INT  NOT NULL,
  valid_from DATE NOT NULL,
  created_at TIMESTAMP DEFAULT now()
);
```

要点：
- **原始消息保留 `raw` JSONB**，指标算错了可以重算，不用重新导出。
- **指标按时间窗切片存**（周/月），才能看趋势；只存一份最新快照就没有动量分析。
- **画像版本化**，才能看「这个供应商这季度是不是变差了」。
- 指标层与画像层分开存：指标是客观数字，画像是模型解读，混在一起后期没法重算。

# EcomEvo Product UI System

## Design read

EcomEvo 是一个给高频运营、审核、客服与风控人员长期使用的 **agentic operations desk**。用户可能连续数小时浏览任务、证据、进度与高影响操作，因此界面首先服务于：

1. 快速定位当前任务；
2. 快速理解 Agent 正在做什么；
3. 快速判断证据是否足够；
4. 在高影响动作发生前获得清晰控制权；
5. 让文字、图片、视频、音频、文档、表格和日志自然进入同一个任务。

设计不是为了制造“AI 魔法感”，而是为了让自主工作 **可理解、可追踪、可干预**。

## Visual character

关键词：**Porcelain / Graphite / Oxide / Jade**。

- 暖瓷白工作面：降低长时间阅读的冷白刺激；
- 石墨结构：承载导航、任务拓扑与高密度信息；
- 氧化橙：唯一主动作色，用于提交、当前任务与关键自主状态；
- 玉石绿：只用于已验证、已完成、连接健康等正向状态；
- 琥珀与深红：只承担需要注意和高风险语义。

这套配色刻意避开常见 AI 产品的蓝紫渐变、霓虹光晕和大面积玻璃态。

## Color tokens

产品 CSS 使用 OKLCH，以便让亮度、色度和对比关系更可控。

```css
--nav: oklch(22% .018 55);
--ink: oklch(24% .018 60);
--canvas: oklch(96.6% .011 78);
--paper: oklch(98.6% .007 78);
--accent: oklch(61% .155 42);
--accent-2: oklch(53% .145 42);
--success: oklch(55% .105 158);
--warning: oklch(61% .13 72);
--danger: oklch(52% .155 28);
```

禁止纯黑和纯白作为大面积表面。中性色必须带轻微暖色倾向。

## Typography

界面使用系统级高质量可变字体优先栈，不从第三方 CDN 拉字体：

```text
Aptos / SF Pro / Segoe UI Variable / PingFang SC / Noto Sans CJK SC / Microsoft YaHei / system-ui
```

角色：

- Display：任务标题、首屏主标题、重要业务结论；
- UI Text：操作、正文、说明；
- Mono：阶段标签、计数、时间、运行状态与技术性短标签。

规则：

- 中文正文不低于 12px；
- 10px 以下只允许出现在元信息；
- 标题通过字号和字重建立层级，不用渐变文字；
- 长正文宽度保持在约 65–75 个西文字符的阅读尺度；
- 大标题采用更紧字距，但正文不做过度 tracking。

## Layout

### Desktop

三块区域各自承担明确职责：

- 左：业务场景与历史任务；
- 中：任务目标、多模态输入、对话和结果；
- 右：任务轨迹、证据、执行控制与资料。

中间不是聊天窗口，而是 **任务工作面**。

### Welcome state

禁止四张等尺寸 feature cards。首屏采用非对称布局：

- 左侧：目标输入说明 + 不等尺寸业务任务入口；
- 右侧：Goal → Evidence → Review → Verify → Action 的任务路径图；
- 任务路径图是信息架构提示，不模拟隐藏 chain-of-thought。

大屏显示路径图；窄屏自动收敛到单列任务入口。

### Right control surface

右侧不叫“详情”，叫 **任务控制面**，四个 tab：

- 轨迹；
- 证据；
- 执行；
- 资料。

百分比只是辅助状态，不作为视觉英雄指标。用户真正需要的是可读的任务轨迹。

## Multimodal input

输入区是页面的主控制器，不是底部附属文本框。

输入框头部明确标识 **多模态输入**。图片、视频、音频、文档与表格按钮属于同一个输入 dock；拖拽文件时整页进入明确的 drop target 状态。

资料与问题属于同一个任务上下文，因此后续追问不用重新上传或重新描述背景。

## Motion

所有动效必须回答一个问题：**什么状态发生了变化？**

- hover / focus：140–180ms；
- panel / drawer：180–240ms；
- progress / state：180–260ms；
- easing：`cubic-bezier(.16,1,.3,1)`；
- 不动画宽高等高成本 layout 属性；
- 不使用 bounce / elastic；
- `prefers-reduced-motion` 时关闭空间移动，只保留即时状态反馈。

## Agent UX principles

### 1. Goal before prompt

界面语言使用“目标”“任务”“资料”“证据”“执行”，避免把产品降格成聊天机器人。

### 2. Visible work, not fake thinking

可以展示：

- 正在核对什么；
- 使用了哪类证据；
- 是否发生补证、重规划、专项复核；
- 当前是否还缺资料。

不展示隐藏推理过程或长篇内部思维。

### 3. Evidence before confidence

业务结论旁优先展示证据和缺口，不使用夸张的“AI 置信度大数字”代替事实。

### 4. Approval is a control surface

高影响动作卡必须包含：

- 动作是什么；
- 会改变什么业务状态；
- 风险级别；
- 当前执行状态；
- 明确的确认和拒绝按钮。

`uncertain` 必须有独立视觉与文案，不能伪装成成功。

### 5. Recovery must stay visible

断线、失败、待补证和下游结果不确定时，界面要告诉用户“下一步怎么办”，而不是只显示错误码。

## Anti-slop rules

禁止：

- AI 紫、蓝紫 mesh gradient；
- 装饰性渐变文字；
- 默认 glassmorphism；
- 三到四张完全等尺寸的 feature card 墙；
- 用彩色左边框当所有状态的默认表达；
- 大号数字 + 小标签的 SaaS hero metric 模板；
- 为了“科技感”无限循环的发光、漂浮、粒子动画；
- 多层卡片套卡片；
- 把一个信息架构问题优先做成 modal；
- 在产品界面暴露底层模型或服务品牌。

## Accessibility

- 所有按钮必须有可见 focus 状态；
- drawer 和 tab 保留键盘导航；
- 状态不能只靠颜色表达；
- 重要文字对比达到日常 B2B 长时间使用要求；
- `prefers-reduced-motion` 被完整尊重；
- 390px 宽度仍必须能够完成任务输入、资料添加和执行确认。

## Known interaction fixes

本轮 UI 审计中特别修复或防御：

- 动作确认后的 toast 不再把 `approved / uncertain / failed` 错写成“已完成”；
- 产品页面不再直接显示底层模型/服务品牌；
- 多模态输入从辅助功能提升为一级入口；
- 历史的等尺寸任务卡墙被非对称任务入口替代；
- 右栏从普通详情栏调整为 Agent 控制面；
- 网络/任务创建失败通过全局兜底提示避免无反馈；
- 旧的侧边彩条式 active state 在新视觉层中被完整面和边界状态替代。

## Shipping rule

任何后续 UI 改动都要同时通过四项检查：

1. 业务含义是否更清楚；
2. 自主状态是否更容易理解；
3. 高影响动作是否更难误触；
4. 视觉是否仍然属于 EcomEvo，而不是套用常见 AI 模板。

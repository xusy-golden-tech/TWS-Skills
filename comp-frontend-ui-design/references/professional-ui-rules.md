# Professional UI Rules

> 通用专业 UI 规则。普通设计任务只读 `SKILL.md`；需要审查细节或制定设计系统时再读本文件。

## Icons & Visual Elements

| Rule | Do | Avoid |
|------|----|-------|
| 默认图标库 | Phosphor / Heroicons / Lucide 等矢量图标 | emoji 做导航或设置图标 |
| Vector-only assets | SVG 或平台矢量图标 | 模糊 PNG 图标 |
| Touch target | 图标按钮点击区 ≥ 44×44px | 小图标裸点击 |
| Stable states | 状态变化只改颜色、透明度、阴影 | 按压时布局抖动 |
| Stroke consistency | 同层级统一笔画宽度 | 混用粗细和填充风格 |
| Icon contrast | 与背景有足够对比度 | 低对比度浅灰图标 |

## Interaction

| Rule | Do | Avoid |
|------|----|-------|
| Tap feedback | 80-150ms 内显示反馈 | 点击无响应 |
| Animation timing | 150-300ms，使用平台惯例缓动 | 瞬时跳变或 > 500ms |
| Disabled state | 低透明度 + disabled 语义 | 看起来可点但没反应 |
| Gesture conflicts | 每区域一个主手势 | 嵌套 tap/drag 冲突 |
| Native semantics | 使用 Button / Pressable 等语义控件 | 用 div/span 扮演控件 |

## Light / Dark Mode

| Rule | Do | Avoid |
|------|----|-------|
| Surface readability | 前景与背景层级清楚 | 透明度过高导致糊在一起 |
| Text contrast | 主文字 ≥ 4.5:1 | 低对比度灰字 |
| State parity | 状态色两种主题都清楚 | 只为一种主题定义状态 |
| Token theming | 使用语义 token per theme | 组件内硬编码 hex |
| Modal scrim | 40-60% 遮罩隔离前景 | 弱遮罩导致前后景竞争 |

## Layout & Spacing

| Rule | Do | Avoid |
|------|----|-------|
| Safe area | 固定头尾栏避让安全区域 | UI 被刘海或手势区遮挡 |
| 8dp rhythm | 使用 4/8dp 间距体系 | 随意间距 |
| Content width | 桌面限制最大宽度 | 长文撑满屏幕 |
| Adaptive gutters | 大屏增加水平留白 | 所有设备用同一窄间距 |
| Fixed coexistence | 列表加 content inset | 内容被 sticky 头尾遮挡 |

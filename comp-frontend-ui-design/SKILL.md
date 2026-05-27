---
name: frontend-ui-design
description: 前端 UI/UX 设计。基于 ui-ux-pro-max 设计智能引擎生成设计系统、配色、字体、布局、交互和可访问性规则；长规则表按需读取 references
---

# 前端 UI 设计

## 核心原则

**先确定设计系统，再写代码。**

UI 设计不是随便选个颜色。它要先回答：产品适合什么风格，配色和字体如何服务用户，布局和状态如何让人稳定完成任务。

## 触发条件

```
任务涉及以下任一 → 必须加载此 skill：
- 新页面设计（Landing Page、Dashboard、Admin、SaaS、Mobile App）
- 创建或重构 UI 组件（按钮、弹窗、表单、表格、图表等）
- 选择配色方案、字体系统、间距规范或布局体系
- 审查 UI 代码的用户体验、可访问性或视觉一致性
- 实现导航结构、动效或响应式行为
- 做产品层级的设计决策（风格、信息层级、品牌表达）

纯后端 / API / 数据库 / 基础设施工作 → 不加载。
```

## 工作流程

### Step 1：分析需求

提取产品类型、目标用户、使用场景、风格关键词和技术栈。没有技术栈时，默认按 HTML + Tailwind 产出可执行设计建议。

### Step 2：生成设计系统（必须）

```bash
python comp-frontend-ui-design/scripts/search.py "<产品类型> <行业> <关键词>" --design-system -p "项目名"
```

从仓库根目录运行时使用上面的路径；Windows 也可使用 `py -3 comp-frontend-ui-design/scripts/search.py ...`。如果已经进入本 skill 目录，则改用 `python scripts/search.py ...`。

这一步会并行搜索产品、风格、配色、落地页、字体等数据源，输出布局模式、风格、配色、字体、动效和反模式。

### Step 2b：持久化设计系统（推荐）

```bash
python comp-frontend-ui-design/scripts/search.py "<query>" --design-system --persist -p "Project Name"
python comp-frontend-ui-design/scripts/search.py "<query>" --design-system --persist -p "Project Name" --page "dashboard"
```

产出结构：

```text
design-system/{project}/
├── MASTER.md
└── pages/
    └── dashboard.md
```

后续编码时先查 `pages/{page}.md`，有则以它为准；没有则用 `MASTER.md`。

### Step 3：按需补充搜索

```bash
python comp-frontend-ui-design/scripts/search.py "<关键词>" --domain <domain> [-n <数量>]
python comp-frontend-ui-design/scripts/search.py "<关键词>" --stack <stack>
```

常用 domain：`product`, `style`, `color`, `typography`, `google-fonts`, `icons`, `chart`, `ux`, `landing`, `react`, `web`。

常用 stack：`react`, `nextjs`, `vue`, `nuxtjs`, `svelte`, `astro`, `swiftui`, `react-native`, `flutter`, `html-tailwind`, `shadcn`, `threejs`。

完整规则和清单不要默认预加载，按需读取：

- UX 规则全集：`references/ux-guidelines.md`
- 交付检查清单：`references/delivery-checklist.md`
- 专业 UI 通用规则：`references/professional-ui-rules.md`

### 维护脚本说明

`comp-frontend-ui-design/data/_sync_all.py` 是数据维护脚本，会重写部分 CSV 文件。普通 UI 设计任务不要运行它；只有在维护数据源、确认备份或已准备好审查 CSV diff 时才运行。

## 不可让步 UI Gate

交付任何 UI 设计或 UI 代码前，至少检查这些门禁。需要完整细项时再读取 references。

```
□ 已生成或读取设计系统（MASTER.md / 页面覆盖文件）
□ 页面/组件包含 loading、empty、error、normal 状态
□ 正文对比度 ≥ 4.5:1，大文本和非文本 UI ≥ 3:1
□ 交互元素有可见 focus、hover/pressed/disabled 状态
□ 触控目标 ≥ 44×44px，移动端无水平滚动
□ 图标使用同一矢量图标体系，不用 emoji 当功能图标
□ 颜色不是唯一信息载体，错误/成功状态有文字或图标
□ 表单有 label、错误提示和恢复路径
□ 动效尊重 prefers-reduced-motion，关键操作不被动画阻塞
□ 固定头尾栏避让安全区域，不遮挡滚动内容
□ 响应式至少覆盖 375 / 768 / 1024 / 1440px
□ 图表或数据密集界面提供可读标签、单位和空/错状态
```

## 输出格式

```markdown
### 页面/组件：{name}
- 功能：{做什么}
- 布局：{页面区域的划分}
- 风格：{选定风格及理由}
- 配色：{主色/辅色/强调色/背景/文字}
- 字体：{标题/正文字体}
- 状态：{loading / empty / error / normal}
- 交互：{点击→跳转、输入→搜索等}
- 数据：{来自哪个 API / Store}
- 反模式：{需要避免的设计错误}
- 验证：{已检查的 UI Gate / references}
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「这个页面很标准，不用设计」 | 标准页面也要确认状态、层级、响应式和可访问性 |
| 「先写代码再调样式」 | 没有设计系统会让配色、字体和间距在后期反复返工 |
| 「UI 不用搜索，我知道什么好看」 | 先用数据源和规则生成候选，再由产品语境取舍 |

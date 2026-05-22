---
name: frontend-ui-design
description: 前端 UI/UX 设计。基于 ui-ux-pro-max 设计智能引擎，提供 80+ 种 UI 风格、161 组配色、70+ 组字体搭配、99 条 UX 规范、25 种图表类型、16 种技术栈指导。可生成完整设计系统并持久化
---

# 前端 UI 设计

## 核心原则

**先确定设计系统，再写代码。**

UI 设计不是随便选个颜色。是回答：这个产品适合什么风格？配色方案是什么？字体怎么搭？交互怎么定？

## 触发条件

```
任务涉及以下任一 → 必须加载此 Skill：
- 新页面设计（Landing Page、Dashboard、Admin、SaaS、Mobile App）
- 创建或重构 UI 组件（按钮、弹窗、表单、表格、图表等）
- 选择配色方案、字体系统、间距规范或布局体系
- 审查 UI 代码的用户体验、可访问性或视觉一致性
- 实现导航结构、动效或响应式行为
- 做产品层级的设计决策（风格、信息层级、品牌表达）

纯后端 / API / 数据库 / 基础设施工作 → 不加载。
```

---

## 工作流程

### Step 1：分析需求

从用户请求中提取：
- **产品类型**：SaaS、电商、社交、工具、金融等
- **目标用户**：C 端消费者 / B 端企业，年龄层，使用场景
- **风格关键词**：简洁、现代、暗色、沉浸、专业等
- **技术栈**：React、Vue、React Native、Flutter 等（默认 HTML + Tailwind）

### Step 2：生成设计系统（必须）

```bash
python3 .claude/skills/comp-frontend-ui-design/scripts/search.py "<产品类型> <行业> <关键词>" --design-system -p "项目名"
```

这一步会：
1. 并行搜索 5 个域（产品、风格、配色、落地页、字体）
2. 应用 161 条行业推理规则
3. 输出完整设计系统：布局模式、风格、配色、字体、动效
4. 包含需要避免的反模式

**示例：**
```bash
python3 .claude/skills/comp-frontend-ui-design/scripts/search.py "beauty spa wellness service" --design-system -p "Serenity Spa"
```

### Step 2b：持久化设计系统（推荐）

```bash
# 保存到 design-system/{project}/MASTER.md
python3 .claude/skills/comp-frontend-ui-design/scripts/search.py "<query>" --design-system --persist -p "Project Name"

# 同时创建页面级覆盖文件
python3 .claude/skills/comp-frontend-ui-design/scripts/search.py "<query>" --design-system --persist -p "Project Name" --page "dashboard"
```

产出结构：
```
design-system/{project}/
├── MASTER.md           ← 全局设计规范
└── pages/
    └── dashboard.md    ← 页面级覆盖（仅偏差部分）
```

**使用方式：** 后续编码时先查 pages/{page}.md，有则以它为准；没有则用 MASTER.md。

### Step 3：按需补充搜索

```bash
python3 .claude/skills/comp-frontend-ui-design/scripts/search.py "<关键词>" --domain <域> [-n <数量>]
```

| 需求 | 域 | 示例 |
|------|-----|------|
| 产品类型推荐 | `product` | `--domain product "SaaS enterprise"` |
| UI 风格选项（含 AI 提示词/CSS 关键词） | `style` | `--domain style "glassmorphism dark"` |
| 配色方案 | `color` | `--domain color "fintech professional"` |
| 字体搭配 | `typography` | `--domain typography "elegant modern"` |
| Google Fonts 查询 | `google-fonts` | `--domain google-fonts "serif"` |
| 图标推荐 | `icons` | `--domain icons "navigation"` |
| 图表推荐 | `chart` | `--domain chart "real-time dashboard"` |
| UX 最佳实践 | `ux` | `--domain ux "animation accessibility"` |
| 落地页结构 | `landing` | `--domain landing "hero social-proof"` |
| React/Next.js 性能 | `react` | `--domain react "rerender memo list"` |
| App 界面可访问性 | `web` | `--domain web "accessibilityLabel touch"` |

### Step 4：技术栈指南

```bash
python3 .claude/skills/comp-frontend-ui-design/scripts/search.py "<关键词>" --stack <技术栈>
```

可用技术栈：`react`, `nextjs`, `vue`, `nuxtjs`, `nuxt-ui`, `svelte`, `astro`, `swiftui`, `react-native`, `flutter`, `html-tailwind`, `shadcn`, `jetpack-compose`, `angular`, `laravel`, `threejs`

---

## 输出格式

完成设计后，输出以下结构供编码阶段使用：

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
```

---

## 快速参考（10 类 UX 规范）

### 1. 可访问性 CRITICAL

- `color-contrast` — 正文对比度 ≥ 4.5:1（大文本 ≥ 3:1）（Material Design）
- `focus-states` — 交互元素可见焦点环（2–4px）（Apple HIG, MD）
- `alt-text` — 有意义的图片需要描述性 alt 文字
- `aria-labels` — 纯图标按钮需要 aria-label；原生用 accessibilityLabel（Apple HIG）
- `keyboard-nav` — Tab 顺序匹配视觉顺序；完整键盘支持（Apple HIG）
- `form-labels` — 使用 label 标签关联 for 属性
- `skip-links` — 提供跳转到主内容的链接（键盘用户）
- `heading-hierarchy` — h1→h6 顺序递进，不跳级
- `color-not-only` — 不能仅靠颜色传达信息（需加图标/文字）
- `dynamic-type` — 支持系统字体缩放；文字增长时不截断（Apple Dynamic Type, MD）
- `reduced-motion` — 尊重 prefers-reduced-motion；在开启时减少/禁用动画（Apple, MD）
- `voiceover-sr` — 有意义的 accessibilityLabel/accessibilityHint；逻辑朗读顺序（Apple HIG, MD）
- `escape-routes` — 模态框和多步流程提供取消/返回出口（Apple HIG）
- `keyboard-shortcuts` — 保留系统和无障碍快捷键；为拖拽提供键盘替代方案（Apple HIG）

### 2. 触控与交互 CRITICAL

- `touch-target-size` — 最小 44×44pt（Apple）/ 48×48dp（Material）；必要时扩展点击区域
- `touch-spacing` — 触控目标间距 ≥ 8px/8dp（Apple HIG, MD）
- `hover-vs-tap` — 主交互用点击/触摸；不要仅依赖 hover
- `loading-buttons` — 异步操作时禁用按钮，显示 spinner 或进度
- `error-feedback` — 在问题附近显示明确错误信息
- `cursor-pointer` — 可点击元素加 cursor: pointer（Web）
- `gesture-conflicts` — 主内容区避免水平滑动；优先垂直滚动
- `tap-delay` — 使用 touch-action: manipulation 消除 300ms 延迟（Web）
- `standard-gestures` — 使用平台标准手势；不要重新定义（如滑动返回、捏合缩放）（Apple HIG）
- `system-gestures` — 不要拦截系统手势（控制中心、返回滑动等）（Apple HIG）
- `press-feedback` — 按压时有视觉反馈（ripple/高亮；MD state layers）
- `haptic-feedback` — 确认和重要操作使用触觉反馈；避免过度使用（Apple HIG）
- `gesture-alternative` — 关键操作不要仅依赖手势；始终提供可见控件
- `safe-area-awareness` — 主要触控目标远离刘海、灵动岛、手势栏和屏幕边缘
- `no-precision-required` — 避免要求像素级精准点击小图标或细边缘
- `swipe-clarity` — 滑动操作必须有明确暗示（箭头、标签、教程）
- `drag-threshold` — 拖拽前设置移动阈值，避免误触

### 3. 性能 HIGH

- `image-optimization` — WebP/AVIF + 响应式图片（srcset/sizes）+ 非关键资源懒加载
- `image-dimension` — 声明 width/height 或使用 aspect-ratio 防止布局偏移（CLS）
- `font-loading` — font-display: swap/optional 避免 FOIT；预留空间减少布局偏移（MD）
- `font-preload` — 仅预加载关键字体；不要每个变体都预加载
- `critical-css` — 首屏 CSS 优先（内联或尽早加载）
- `lazy-loading` — 非首屏组件通过 dynamic import / 路由级分割懒加载
- `bundle-splitting` — 按路由/功能分割代码（React Suspense / Next.js dynamic），减少首屏 TTI
- `third-party-scripts` — 第三方脚本 async/defer 加载；审计并移除不必要的（MD）
- `reduce-reflows` — 避免频繁读写布局；批量 DOM 读取再写入
- `content-jumping` — 为异步内容预留空间，避免布局跳动（CLS）
- `lazy-load-below-fold` — 首屏以下的图片和重型媒体用 loading="lazy"
- `virtualize-lists` — 50+ 项列表虚拟滚动，提升内存和滚动性能
- `main-thread-budget` — 每帧工作 ≤ ~16ms（60fps）；重任务移到主线程外（HIG, MD）
- `progressive-loading` — 超过 1s 的操作用骨架屏/shimmer，不用阻塞式 spinner（Apple HIG）
- `input-latency` — 点击/滚动的输入延迟 ≤ ~100ms（Material 响应标准）
- `tap-feedback-speed` — 点击后 100ms 内提供视觉反馈（Apple HIG）
- `debounce-throttle` — 高频事件（scroll、resize、input）使用防抖/节流
- `offline-support` — 提供离线状态提示和基本降级（PWA / 移动端）
- `network-fallback` — 慢网络提供降级模式（低分辨率图片、减少动画）

### 4. 风格选择 HIGH

- `style-match` — 风格匹配产品类型（用 `--design-system` 获取推荐）
- `consistency` — 全页面风格统一
- `no-emoji-icons` — 使用 SVG 图标（Heroicons, Lucide），不用 emoji
- `color-palette-from-product` — 按产品/行业选择配色（搜索 `--domain color`）
- `effects-match-style` — 阴影、模糊、圆角与选定风格对齐（glass / flat / clay 等）
- `platform-adaptive` — 尊重平台惯例（iOS HIG vs Material）：导航、控件、字体、动效
- `state-clarity` — hover/pressed/disabled 状态视觉上清晰区分（Material state layers）
- `elevation-consistent` — 卡片、底部弹窗、模态框使用一致的阴影/层级体系
- `dark-mode-pairing` — 明暗主题一起设计，保持品牌、对比度和风格一致
- `icon-style-consistent` — 全产品使用同一图标集/视觉语言（笔画宽度、圆角）
- `system-controls` — 优先使用原生/系统控件；仅在品牌需要时自定义（Apple HIG）
- `blur-purpose` — 模糊用于表示背景 dismiss（模态、底部弹窗），不当装饰（Apple HIG）
- `primary-action` — 每个屏幕只有一个主 CTA；次要操作视觉上从属（Apple HIG）

### 5. 布局与响应式 HIGH

- `viewport-meta` — width=device-width initial-scale=1（永远不要禁用缩放）
- `mobile-first` — 移动优先设计，逐级放大到平板和桌面
- `breakpoint-consistency` — 使用系统化断点（375 / 768 / 1024 / 1440）
- `readable-font-size` — 移动端正文 ≥ 16px（避免 iOS 自动缩放）
- `line-length-control` — 移动端每行 35–60 字符；桌面端 60–75 字符
- `horizontal-scroll` — 移动端禁止水平滚动；内容适配视口宽度
- `spacing-scale` — 使用 4pt/8dp 递增间距体系（Material Design）
- `touch-density` — 组件间距适合触控：不拥挤，不导致误触
- `container-width` — 桌面端一致的 max-width（max-w-6xl / 7xl）
- `z-index-management` — 定义分层 z-index 体系（0 / 10 / 20 / 40 / 100 / 1000）
- `fixed-element-offset` — 固定导航/底栏必须为下方内容预留安全内边距
- `scroll-behavior` — 避免干扰主滚动的嵌套滚动区域
- `viewport-units` — 移动端优先使用 min-h-dvh 而非 100vh
- `orientation-support` — 横屏模式下布局可读可操作
- `content-priority` — 移动端优先展示核心内容；折叠或隐藏次要内容
- `visual-hierarchy` — 通过大小、间距、对比建立层级——而非仅靠颜色

### 6. 字体与配色 MEDIUM

- `line-height` — 正文行高 1.5-1.75
- `line-length` — 每行限制 65-75 字符
- `font-pairing` — 标题/正文字体个性匹配
- `font-scale` — 统一字号体系（12 14 16 18 24 32）
- `contrast-readability` — 浅色背景用深色文字（如 slate-900 on white）
- `text-styles-system` — 使用平台字体体系：iOS Dynamic Type / Material 5 type roles（HIG, MD）
- `weight-hierarchy` — 用 font-weight 强化层级：粗体标题（600–700），常规正文（400），中等标签（500）（MD）
- `color-semantic` — 定义语义色彩 token（primary, secondary, error, surface, on-surface），不在组件里写 raw hex（Material）
- `color-dark-mode` — 暗色模式用去饱和/更亮的色调变体，不反色；单独测试对比度（HIG, MD）
- `color-accessible-pairs` — 前景/背景对比度 ≥ 4.5:1（AA）或 7:1（AAA）（WCAG, MD）
- `color-not-decorative-only` — 功能色（错误红、成功绿）必须附带图标/文字（HIG, MD）
- `truncation-strategy` — 优先换行而非截断；截断时用省略号并提供 tooltip/展开查看全文（Apple HIG）
- `letter-spacing` — 尊重平台默认字间距；避免正文使用紧凑 tracking（HIG, MD）
- `number-tabular` — 数据列、价格、计时器使用等宽/制表数字，防止布局偏移
- `whitespace-balance` — 有意使用留白分组相关项、分隔区域；避免视觉杂乱（Apple HIG）

### 7. 动效 MEDIUM

- `duration-timing` — 微交互 150–300ms；复杂过渡 ≤ 400ms；避免 > 500ms（MD）
- `transform-performance` — 仅动画 transform/opacity；不动画 width/height/top/left
- `loading-states` — 加载超过 300ms 时显示骨架屏或进度指示器
- `excessive-motion` — 每个视图最多动画 1-2 个关键元素
- `easing` — 进入用 ease-out，退出用 ease-in；UI 过渡避免 linear
- `motion-meaning` — 每个动画必须表达因果关系，不仅是装饰（Apple HIG）
- `state-transition` — 状态变化（hover / active / expanded / collapsed / modal）应平滑过渡，不跳变
- `continuity` — 页面/屏幕切换保持空间连续性（共享元素、方向性滑动）（Apple HIG）
- `parallax-subtle` — 谨慎使用视差；必须尊重 reduced-motion，不引起晕眩（Apple HIG）
- `spring-physics` — 优先使用弹簧/物理曲线而非线性或 cubic-bezier，感觉更自然（Apple HIG）
- `exit-faster-than-enter` — 退场动画比入场短（约入场的 60–70%），感觉更灵敏（MD）
- `stagger-sequence` — 列表/网格项交错入场，每项间隔 30–50ms；避免同时出现或过慢（MD）
- `shared-element-transition` — 屏幕间使用共享元素/hero 过渡保持视觉连续（MD, HIG）
- `interruptible` — 动画可中断；用户点击/手势立即取消进行中的动画（Apple HIG）
- `no-blocking-animation` — 动画期间永远不要阻止用户输入；UI 保持可交互（Apple HIG）
- `fade-crossfade` — 同一容器内的内容替换使用交叉淡入（MD）
- `scale-feedback` — 可点击卡片/按钮按下时微缩放（0.95–1.05）；松开恢复（HIG, MD）
- `gesture-feedback` — 拖拽、滑动、捏合必须提供跟踪手指的实时视觉响应（MD Motion）
- `hierarchy-motion` — 用 translate/scale 方向表达层级：从下方进入=更深，向上退出=返回（MD）
- `motion-consistency` — 全局统一 duration/easing token；所有动画共享相同节奏和感觉
- `opacity-threshold` — 淡出元素不应停留在 opacity 0.2 以下；要么完全消失，要么保持可见
- `modal-motion` — 模态框/底部弹窗应从触发源动画进入（缩放+淡入或滑入），建立空间上下文（HIG, MD）
- `navigation-direction` — 前进导航向左/上动画；后退向右/下——保持方向逻辑一致（HIG）
- `layout-shift-avoid` — 动画不得引起布局回流或 CLS；用 transform 改变位置

### 8. 表单与反馈 MEDIUM

- `input-labels` — 每个输入框有可见 label（不仅是 placeholder）
- `error-placement` — 错误信息显示在相关字段下方
- `submit-feedback` — 提交时先显示加载，再显示成功/错误状态
- `required-indicators` — 标记必填字段（如星号）
- `empty-states` — 无内容时显示有用提示和操作引导
- `toast-dismiss` — Toast 自动消失 3-5 秒
- `confirmation-dialogs` — 破坏性操作前确认
- `input-helper-text` — 复杂输入框下方提供持久帮助文字，不仅是 placeholder（MD）
- `disabled-states` — 禁用元素使用降低透明度（0.38–0.5）+ 光标变化 + 语义属性（MD）
- `progressive-disclosure` — 渐进展示复杂选项；不要一开始就淹没用户（Apple HIG）
- `inline-validation` — blur 时验证（非按键时）；用户输入完成后再显示错误（MD）
- `input-type-keyboard` — 使用语义化 input type（email, tel, number）触发正确的移动键盘（HIG, MD）
- `password-toggle` — 密码字段提供显示/隐藏切换（MD）
- `autofill-support` — 使用 autocomplete / textContentType 属性让系统自动填充（HIG, MD）
- `undo-support` — 允许撤销破坏性或批量操作（如"撤销删除" toast）（Apple HIG）
- `success-feedback` — 完成操作后提供简短的视觉反馈（勾选、toast、颜色闪烁）（MD）
- `error-recovery` — 错误信息必须包含明确的恢复路径（重试、编辑、帮助链接）（HIG, MD）
- `multi-step-progress` — 多步流程显示步骤指示器或进度条；允许返回（MD）
- `form-autosave` — 长表单应自动保存草稿，防止意外关闭丢失数据（Apple HIG）
- `sheet-dismiss-confirm` — 有未保存更改的 sheet/modal 关闭前确认（Apple HIG）
- `error-clarity` — 错误信息说明原因 + 如何修复（不只是"输入无效"）（HIG, MD）
- `field-grouping` — 逻辑分组相关字段（fieldset/legend 或视觉分组）（MD）
- `read-only-distinction` — 只读状态应视觉上和语义上与禁用状态区分（MD）
- `focus-management` — 提交出错后自动聚焦第一个无效字段（WCAG, MD）
- `error-summary` — 多个错误时在顶部显示摘要并链接到各字段（WCAG）
- `touch-friendly-input` — 移动端输入框高度 ≥ 44px 满足触控要求（Apple HIG）
- `destructive-emphasis` — 破坏性操作使用语义危险色（红色），与主操作视觉分离（HIG, MD）
- `toast-accessibility` — Toast 不抢焦点；用 aria-live="polite" 通知屏幕阅读器（WCAG）
- `aria-live-errors` — 表单错误用 aria-live 区域或 role="alert" 通知屏幕阅读器（WCAG）
- `contrast-feedback` — 错误和成功状态颜色对比度 ≥ 4.5:1（WCAG, MD）
- `timeout-feedback` — 请求超时必须显示明确反馈并提供重试（MD）

### 9. 导航模式 HIGH

- `bottom-nav-limit` — 底部导航最多 5 项；图标配文字标签（Material Design）
- `drawer-usage` — 抽屉/侧边栏用于二级导航，不用于主操作（Material Design）
- `back-behavior` — 返回导航可预测且一致；保留滚动位置和状态（Apple HIG, MD）
- `deep-linking` — 所有关键屏幕必须可通过深度链接/URL 到达（Apple HIG, MD）
- `tab-bar-ios` — iOS：顶层导航使用底部 Tab Bar（Apple HIG）
- `top-app-bar-android` — Android：主结构使用 Top App Bar + 导航图标（Material Design）
- `nav-label-icon` — 导航项必须同时有图标和文字标签；仅图标导航损害可发现性（MD）
- `nav-state-active` — 当前位置在导航中视觉高亮（颜色、粗细、指示器）（HIG, MD）
- `nav-hierarchy` — 一级导航（tabs/底栏）和二级导航（抽屉/设置）必须清晰分离（MD）
- `modal-escape` — 模态框和底部弹窗提供明确的关闭/取消入口；移动端可下滑关闭（Apple HIG）
- `search-accessible` — 搜索易于到达（顶栏或 tab）；提供最近/建议查询（MD）
- `breadcrumb-web` — Web：3+ 层级深度使用面包屑辅助定位（MD）
- `state-preservation` — 返回时恢复之前的滚动位置、筛选状态和输入（HIG, MD）
- `gesture-nav-support` — 支持系统手势导航（iOS 滑动返回、Android 预测返回）不冲突（HIG, MD）
- `tab-badge` — 导航项上谨慎使用徽标提示未读/待处理；用户访问后清除（HIG, MD）
- `overflow-menu` — 操作超出空间时使用溢出/更多菜单，不硬塞（MD）
- `bottom-nav-top-level` — 底部导航仅用于顶层屏幕；不要在其中嵌套子导航（MD）
- `adaptive-navigation` — 大屏（≥1024px）优先侧边栏；小屏用底部/顶部导航（Material Adaptive）
- `back-stack-integrity` — 永远不要静默重置导航栈或意外跳回首页（HIG, MD）
- `navigation-consistency` — 导航位置在所有页面保持一致；不因页面类型变化
- `avoid-mixed-patterns` — 同一层级不要混用 Tab + 侧边栏 + 底栏
- `modal-vs-navigation` — 模态框不用于主导航流程；会打断用户路径（HIG）
- `focus-on-route-change` — 页面切换后将焦点移到主内容区，供屏幕阅读器用户（WCAG）
- `persistent-nav` — 核心导航在深层页面也可达；不要在子流程中完全隐藏（HIG, MD）
- `destructive-nav-separation` — 危险操作（删除账户、退出登录）与正常导航项视觉和空间分离（HIG, MD）
- `empty-nav-state` — 导航目标不可用时解释原因，不要静默隐藏（MD）

### 10. 图表与数据 LOW

- `chart-type` — 数据类型匹配图表（趋势→折线，对比→柱状，比例→饼图/环形图）
- `color-guidance` — 使用可访问配色；避免仅红绿配对（色盲用户）（WCAG, MD）
- `data-table` — 提供表格替代方案；图表本身不友好屏幕阅读器（WCAG）
- `pattern-texture` — 用图案、纹理或形状补充颜色，无色也可区分数据（WCAG, MD）
- `legend-visible` — 始终显示图例；靠近图表放置，不要在滚动折叠下方（MD）
- `tooltip-on-interact` — Web hover 或移动端 tap 时显示 tooltip/数据标签（HIG, MD）
- `axis-labels` — 轴标签含单位，刻度可读；移动端避免截断或旋转标签
- `responsive-chart` — 小屏幕图表重排或简化（如垂直柱状变水平，减少刻度）
- `empty-data-state` — 无数据时显示有意义的空状态（"暂无数据" + 引导），不显示空白图（MD）
- `loading-chart` — 图表数据加载时用骨架屏/shimmer 占位；不显示空坐标轴
- `animation-optional` — 图表入场动画尊重 prefers-reduced-motion；数据立即可读（HIG）
- `large-dataset` — 1000+ 数据点时聚合或采样；提供下钻查看详情（MD）
- `number-formatting` — 轴和标签使用本地化数字、日期、货币格式（HIG, MD）
- `touch-target-chart` — 交互式图表元素（点、扇区）点击区域 ≥ 44pt 或触摸时扩展（Apple HIG）
- `no-pie-overuse` — 超 5 个分类不用饼图/环形图；换柱状图更清晰
- `contrast-data` — 数据线/柱与背景对比度 ≥ 3:1；数据文字标签 ≥ 4.5:1（WCAG）
- `legend-interactive` — 图例可点击切换系列显示/隐藏（MD）
- `direct-labeling` — 小数据集直接在图表上标注数值，减少视线移动
- `tooltip-keyboard` — Tooltip 内容必须键盘可达，不仅依赖 hover（WCAG）
- `sortable-table` — 数据表格支持排序，用 aria-sort 标识当前排序状态（WCAG）
- `axis-readability` — 轴刻度不拥挤；保持可读间距，小屏幕自动跳过
- `data-density` — 限制每张图表的信息密度，避免认知过载；需要时拆分多图
- `trend-emphasis` — 强调数据趋势而非装饰；避免遮盖数据的重渐变/阴影
- `gridline-subtle` — 网格线低对比度（如 gray-200），不与数据竞争注意力
- `focusable-elements` — 交互式图表元素（点、柱、扇区）必须可键盘导航（WCAG）
- `screen-reader-summary` — 提供文字摘要或 aria-label 描述图表关键洞察（WCAG）
- `error-state-chart` — 数据加载失败显示错误信息和重试操作，不显示空白/损坏图表
- `export-option` — 数据密集产品提供 CSV/图片导出
- `drill-down-consistency` — 下钻交互保持清晰的返回路径和层级面包屑
- `time-scale-clarity` — 时间序列图表清晰标注时间粒度（日/周/月），可切换

---

## 交付前检查清单

在交付 UI 代码前验证：

### 视觉质量
- [ ] 不用 emoji 做图标（用 SVG）
- [ ] 图标来自同一族、同一风格
- [ ] 品牌资产使用官方资源，比例和间距正确
- [ ] 按压状态不导致布局偏移或抖动
- [ ] 使用语义化主题 token（无硬编码颜色）

### 交互
- [ ] 可点击元素有明确按压反馈
- [ ] 触控目标 ≥ 44×44px
- [ ] 微交互时长 150-300ms
- [ ] 禁用状态视觉清晰（低透明度 + disabled 语义）
- [ ] 屏幕阅读器焦点顺序匹配视觉顺序，交互标签具描述性
- [ ] 手势区域无嵌套/冲突

### 明暗模式
- [ ] 正文对比度 ≥ 4.5:1（两种模式）
- [ ] 次要文字对比度 ≥ 3:1
- [ ] 分隔线/边框在两种模式下都可见
- [ ] Modal/Drawer 遮罩透明度足够隔离前景（40-60% 黑色）
- [ ] 两种模式都实际测试过

### 布局
- [ ] 安全区域避让（头部、底栏、CTA 不被刘海/手势区遮挡）
- [ ] 滚动内容不被固定栏遮挡
- [ ] 375px / 768px / 1024px / 1440px 都正常
- [ ] 无水平滚动
- [ ] 4/8dp 间距节奏
- [ ] 横屏模式下布局可读可操作

### 可访问性
- [ ] 有意义的图片有 alt 文字
- [ ] 表单有 label、错误信息
- [ ] 颜色不是唯一指示器
- [ ] 支持 reduced-motion
- [ ] 无障碍角色/状态（selected, disabled, expanded）正确声明
- [ ] 动态字体大小下布局不崩溃

---

## Common Rules for Professional UI

### Icons & Visual Elements

| Rule | Standard | Avoid | Why |
|------|----------|-------|-----|
| 默认图标库 | Phosphor (`@phosphor-icons/react`)，备选 Heroicons (`@heroicons/react`) | 推荐表中找不到就放弃 | icons.csv 只是常用推荐，不是完整集合 |
| No Emoji as Icons | 矢量图标（SVG） | emoji 做导航/设置图标 | emoji 跨平台不一致，不能被 token 控制 |
| Vector-Only Assets | SVG 或平台矢量图标，支持缩放和主题化 | 位图 PNG 图标（模糊/像素化） | 确保缩放清晰和明暗模式适配 |
| Touch Target | 最小 44×44pt | 小图标不扩展点击区 | 可访问性 + 平台标准 |
| Stable Interaction States | 按压用颜色/透明度/阴影变化，不改变布局边界 | 按压时布局偏移导致周围内容抖动 | 防止交互不稳定，保持感知质量 |
| Icon Sizing | 设计 token 定义（icon-sm, icon-md=24pt, icon-lg） | 随意混 20/24/28pt | 保持视觉节奏 |
| Stroke Consistency | 同层级统一笔画宽度（1.5px 或 2px） | 随意混粗细 | 不一致降低专业感 |
| Filled vs Outline | 同一层级使用一种图标风格（填充或线性） | 同层级混用填充和线性图标 | 语义清晰和风格一致 |
| Icon Alignment | 图标对齐文字基线，保持一致内边距 | 图标错位或间距不一致 | 防止视觉不平衡 |
| Icon Contrast | WCAG 4.5:1（小元素）/ 3:1（大 UI 元素） | 低对比度图标 | 两种主题下都要可见 |

### Interaction (App)

| Rule | Do | Don't |
|------|----|----|
| Tap feedback | 80-150ms 内显示按压反馈（ripple/opacity/elevation） | 无视觉响应 |
| Animation timing | 150-300ms，平台原生缓动 | 瞬时切换或 > 500ms |
| Accessibility Focus | 屏幕阅读器焦点顺序匹配视觉顺序，标签具描述性 | 未标记控件或混乱的焦点遍历 |
| Disabled state | reduced opacity（0.38-0.5）+ disabled 语义属性 | 看起来可点但没反应 |
| Touch Target Minimum | ≥ 44×44pt（iOS）/ ≥ 48×48dp（Android），图标小时扩展点击区 | 小图标不加 hitSlop |
| Gesture conflicts | 每区域一个主手势，避免嵌套 tap/drag 冲突 | 手势重叠导致误操作 |
| Semantic Native Controls | 优先使用原生交互基元（Button, Pressable 等）+ 正确的无障碍角色 | 用通用容器做主控件，无语义 |

### Light/Dark Mode Contrast

| Rule | Do | Don't |
|------|----|----|
| Surface readability | 卡片/表面用足够 opacity/elevation 与背景分离 | 过于透明导致层级模糊 |
| Text contrast (light) | 正文对比度 ≥ 4.5:1 | 低对比度灰色正文 |
| Text contrast (dark) | 主文字 ≥ 4.5:1，次要文字 ≥ 3:1 | 深色模式文字融入背景 |
| Border & Divider Visibility | 分隔线在两种主题下都可见 | 仅一种主题下可见的分隔线 |
| State contrast parity | 按压/聚焦/禁用状态两种主题下都清晰 | 只为一种主题定义状态 |
| Token-driven theming | 语义色彩 token per theme | 组件内硬编码 hex |
| Modal scrim | 40-60% 黑色遮罩，隔离前景 | 弱遮罩导致前后景竞争 |

### Layout & Spacing

| Rule | Do | Don't |
|------|----|----|
| Safe-area Compliance | 固定头部、底部导航、CTA 栏避让安全区域 | UI 被刘海/状态栏/手势区遮挡 |
| 8dp spacing rhythm | 4/8dp 间距体系 | 随意间距无节奏 |
| Consistent content width | 每设备类别可预测内容宽度 | 屏幕间混用任意宽度 |
| Readable Text Measure | 大设备上限制长文宽度，不要撑满全宽 | 平板上文字边缘到边缘 |
| Section spacing hierarchy | 清晰的垂直节奏层级（16/24/32/48） | 相似 UI 层级间距不一致 |
| Adaptive Gutters | 更大宽度/横屏时增加水平内边距 | 所有设备尺寸用相同窄间距 |
| Scroll + fixed coexistence | 列表加 content inset 避免被固定栏遮挡 | 滚动内容被 sticky 头/尾遮挡 |

---

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「这个页面很标准，不用设计」 | 标准页面也要确认每个状态。empty 状态长什么样？|
| 「先写代码再调样式」 | 没有设计系统 → 写完发现配色不对 → 大改 |
| 「就按之前的风格来」 | 之前是什么风格？有 MASTER.md 吗？没有就先生成 |
| 「UI 不用搜索，我知道什么好看」 | 161 条行业推理规则 > 个人审美直觉 |
| 「这个改动太小不需要设计系统」 | 改一个按钮颜色也可能破坏整体配色一致性 |

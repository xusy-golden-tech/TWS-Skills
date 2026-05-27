# UI/UX Guidelines Reference

> 按需读取。`comp-frontend-ui-design/SKILL.md` 只保留热路径 Gate；深度 UI 审查或复杂页面设计时再读取本文件。

## 1. 可访问性 CRITICAL

- `color-contrast`：正文对比度至少 4.5:1，大文本和非文本 UI 至少 3:1。
- `focus-states`：所有可交互元素必须有可见焦点状态。
- `alt-text`：有意义图片必须有描述性 alt；装饰图应明确隐藏。
- `aria-labels`：纯图标按钮需要 aria-label；原生端使用 accessibilityLabel。
- `keyboard-nav`：Tab 顺序匹配视觉顺序，关键流程支持键盘。
- `form-labels`：输入控件使用可见 label，不只依赖 placeholder。
- `color-not-only`：不能只靠颜色传达错误、成功、选中等状态。
- `reduced-motion`：尊重 prefers-reduced-motion，减少或禁用非必要动画。

## 2. 触控与交互 CRITICAL

- `touch-target-size`：触控目标至少 44×44pt / 48×48dp。
- `touch-spacing`：触控目标之间至少 8px 间距。
- `hover-vs-tap`：主交互不能只依赖 hover。
- `loading-buttons`：异步操作时按钮禁用并显示进度或反馈。
- `error-feedback`：错误信息显示在问题附近，并说明恢复路径。
- `gesture-alternative`：关键操作不能只依赖手势，必须有可见控件。
- `safe-area-awareness`：主要触控目标避开刘海、手势栏和屏幕边缘。

## 3. 性能 HIGH

- `image-optimization`：使用 WebP/AVIF、响应式图片和懒加载。
- `image-dimension`：声明尺寸或 aspect-ratio，防止 CLS。
- `font-loading`：使用 font-display: swap/optional，避免 FOIT。
- `lazy-loading`：非首屏组件按路由或功能拆分加载。
- `virtualize-lists`：50+ 项列表考虑虚拟滚动。
- `progressive-loading`：超过 1s 的操作使用骨架屏或分步加载。
- `input-latency`：点击和滚动在 100ms 内给出反馈。

## 4. 风格选择 HIGH

- `style-match`：风格必须匹配产品类型、用户和使用场景。
- `consistency`：全页面风格、图标、阴影、圆角和间距统一。
- `no-emoji-icons`：功能图标使用 SVG 图标库，不使用 emoji。
- `dark-mode-pairing`：明暗主题一起设计，并分别检查对比度。
- `primary-action`：每屏只有一个明确主操作。
- `system-controls`：优先使用平台惯例控件，定制要有理由。

## 5. 布局与响应式 HIGH

- `viewport-meta`：Web 使用 width=device-width initial-scale=1，不禁用缩放。
- `mobile-first`：移动优先，再扩展到平板和桌面。
- `readable-font-size`：移动端正文至少 16px。
- `horizontal-scroll`：移动端禁止无意水平滚动。
- `spacing-scale`：使用 4pt/8dp 间距体系。
- `fixed-element-offset`：固定导航和底栏必须为内容留出安全内边距。
- `content-priority`：移动端优先展示核心信息，折叠次要内容。

## 6. 字体与配色 MEDIUM

- `line-height`：正文行高 1.5-1.75。
- `font-scale`：使用统一字号体系，不随意缩放。
- `font-pairing`：标题和正文字体气质一致。
- `color-semantic`：使用 primary、surface、error 等语义 token。
- `color-dark-mode`：暗色主题单独设计，不直接反色。
- `truncation-strategy`：优先换行，截断时提供 tooltip 或展开。

## 7. 动效 MEDIUM

- `duration-timing`：微交互 150-300ms，复杂过渡不超过 400ms。
- `transform-performance`：动画优先使用 transform 和 opacity。
- `motion-meaning`：动画表达因果关系，不作纯装饰。
- `interruptible`：用户操作可中断动画。
- `no-blocking-animation`：动画期间不阻止关键输入。
- `motion-consistency`：全局复用 duration/easing token。

## 8. 表单与反馈 MEDIUM

- `input-labels`：每个输入框有可见 label。
- `error-placement`：错误信息显示在相关字段附近。
- `submit-feedback`：提交时先显示处理中，再显示成功或失败。
- `disabled-states`：禁用状态有视觉和语义属性。
- `undo-support`：破坏性或批量操作尽量提供撤销。
- `focus-management`：提交失败后聚焦第一个无效字段。
- `toast-accessibility`：Toast 不抢焦点，并用 aria-live 通知。

## 9. 导航模式 HIGH

- `bottom-nav-limit`：底部导航最多 5 项，并配文字标签。
- `back-behavior`：返回行为可预测，并保留状态。
- `nav-state-active`：当前位置必须高亮。
- `nav-hierarchy`：一级导航与二级导航清晰分离。
- `deep-linking`：关键屏幕可通过 URL 或深链到达。
- `adaptive-navigation`：大屏优先侧栏，小屏用底部或顶部导航。
- `modal-vs-navigation`：模态框不承载主导航流程。

## 10. 图表与数据 LOW

- `chart-type`：趋势用折线，对比用柱状，比例谨慎用饼图。
- `color-guidance`：图表不要只用红绿区分。
- `data-table`：复杂图表提供表格或文字摘要。
- `axis-labels`：轴标签含单位，刻度保持可读。
- `empty-data-state`：无数据时显示有意义空状态。
- `large-dataset`：大数据集聚合、采样或提供下钻。
- `screen-reader-summary`：图表提供可读摘要或 aria-label。

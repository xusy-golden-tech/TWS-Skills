---
name: visual-prototype
description: 生成视觉原型。按平台选输出格式：Web 出 HTML demo，非 Web 出 SVG 布局图。用于需求确认阶段的外观演示
---

# 视觉原型

## 核心原则

**一图胜千言。用户看到布局图之前，说的「行」都可能不算。**

---

## 模式选择

```
Web 端（浏览器）        → HTML 模式：独立 HTML 文件，内嵌 CSS
桌面/移动端（原生）      → SVG 模式：独立 SVG 文件
CLI / 终端              → SVG 模式：模拟终端界面的 SVG
嵌入式/硬件屏幕          → SVG 模式：按屏幕尺寸比例画的布局
流程图/架构图            → Mermaid 模式：输出 Mermaid 代码嵌入文档
```

不指定平台时自动按项目技术栈推测。

---

## HTML 模式（Web）

```
生成独立 HTML 文件，内嵌所有 CSS/JS。
可加 Tailwind CDN，不依赖构建工具。
保存到项目 docs/demo/ 下。
双击打开即可预览。
```

外部 CDN 需要用户确认；离线或受限网络环境优先使用内嵌 CSS fallback。

## SVG 模式（非 Web）

```
用 SVG 绘制界面布局草图：
- 绘制窗口/屏幕外框（标注尺寸比例）
- 绘制主要区域（标题栏、内容区、按钮等）
- 用文字标注各区域功能
- 用不同颜色区分区域类型（蓝=导航、灰=内容、绿=操作）
- 保存到项目 docs/demo/ 下。
- 浏览器直接打开 SVG 即可查看。
```

SVG 示例见 `references/examples.md`。普通任务不要把示例预加载进上下文。

## Mermaid 模式（流程图/架构图）

```
输出 Mermaid 代码，嵌入到设计书或文档中。
适用：系统架构、数据流、状态转换。
```

---

## 输出位置

```
项目根目录下的 docs/demo/
├── feature-name.html      ← Web 原型
├── feature-name.svg       ← 非 Web 布局
└── feature-name-flow.md   ← 流程图（可选）
```

输出后告知用户文件路径，预览后再进入编码。

---

## 示例

按需读取 `references/examples.md` 或项目 `demo-mockups/` 目录；主流程不预加载示例。

---

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「这个布局很标准，不用画」 | 标准的布局用户也可能有不同想法 |
| 「文字描述就够了」 | 文字的想象空间太大。看到了才是确认 |
| 「画 SVG 太麻烦」 | 几个形状 + 文字标注，10 分钟的事 |

# 视频爬虫 · Douyin Crawler

> ⚠️ **仅供学习交流使用**

本项目仅用于个人学习 Python 网络编程、反爬分析、桌面 GUI 开发等技术交流。
请勿用于任何商业用途；下载的视频/图片版权归原作者及平台所有，请遵守
《中华人民共和国著作权法》及抖音平台用户协议，切勿二次传播或牟利。
如涉及侵权，请立即删除本项目及所有下载内容。

---

一个适配 2026 年**抖音（Douyin）**最新反爬机制的通用视频/图集下载工具，
同时支持哔哩哔哩、YouTube、TikTok 等 1000+ 站点。包含现代桌面 GUI（圆形卡片、
深/浅色主题、封面缩略图、下载队列）与命令行双入口。

- 抖音 **视频**：自动选择最高可用画质直链下载
- 抖音 **图集（图文笔记）**：自动识别，下载后**按设置转换为 JPG/PNG/WEBP**（默认 JPG）
- 抖音反爬适配：**页面 fetch hook 签名**方案，无需登录即可提取多数公开作品

---

## 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [界面效果](#界面效果)
- [技术实现](#技术实现)
  - [抖音 2026 反爬现状](#抖音-2026-反爬现状)
  - [核心方案：页面 fetch hook 签名](#核心方案页面-fetch-hook-签名)
  - [目标一致性校验](#目标一致性校验)
  - [断点续传与多源回退](#断点续传与多源回退)
  - [图集（图文笔记）识别与下载](#图集图文笔记识别与下载)
  - [多路径提取链](#多路径提取链)
- [项目结构](#项目结构)
- [常见问题](#常见问题)
- [声明](#声明)

---

## 安装

```bash
# 1. 安装依赖（国内镜像）
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    yt-dlp Pillow playwright requests customtkinter

# 2. 安装浏览器内核（抖音提取必需，约 180MB）
python -m playwright install chromium
```

要求 Python 3.10+。

## 快速开始

### 图形界面（推荐）

```bash
python main.py
```

或直接双击 `打开爬虫.bat`（自动检查 Python/依赖/浏览器后启动）。
抖音视频与图集都在界面内下载（无需单独脚本）。

界面操作：粘贴链接（支持抖音分享的**整段文字**，自动提取其中的 URL）→
点击**提取** → 勾选作品 → 选择画质 → **下载选中/全部**。

### 命令行

```bash
# 提取 + 下载（自动最高画质）
python cli.py "https://v.douyin.com/xxxxxx/"

# 只提取元数据
python cli.py "https://v.douyin.com/xxxxxx/" --extract-only

# 指定画质 / 输出目录 / 无浏览器窗口
python cli.py "https://v.douyin.com/xxxxxx/" --quality 720p -o D:/download

# 图集图片保存格式（jpg/png/webp/keep，默认 jpg 自动从 webp 转换）
python cli.py "https://v.douyin.com/xxxxxx/" --image-format png

# 需要登录态时：导入 Cookie 或连接已登录浏览器
python cli.py "https://v.douyin.com/xxxxxx/" --login-cookie cookies.txt
python cli.py "https://v.douyin.com/xxxxxx/" --cdp http://127.0.0.1:9222
```

## 界面效果

- **现代卡片式布局**：深/浅色主题一键切换；圆角输入框、按钮、进度条
- **封面缩略图**：视频/图集封面异步加载，列表与详情面板均展示
- **图集预览**：识别图文帖后详情区提示图片数量与命名规则
- **下载队列**：每个任务实时进度条 + 速度 + 剩余时间，可单选取消
- **信息层级**：主色强调（紫 `#6c5ce7`）+ 次级灰阶，标题/正文/辅助文字分层清晰

## 技术实现

### 抖音 2026 反爬现状

2025-2026 年抖音网页端大幅收紧，旧的常见方案均已失效（实测）：

| 旧方案 | 现状 |
|---|---|
| 分享页 `window._ROUTER_DATA` 服务端数据 | 已改为纯 JS 壳，无任何数据 |
| `iesdouyin.com/web/api/v2/aweme/iteminfo/` | 返回 `encrypt_data_miss`（需签名） |
| 裸调 `aweme/v1/web/aweme/detail/` | **403 blocked**（需 `a_bogus` 签名） |
| 渲染视频页 `/video/{id}` 后拦截网络请求 | 未登录/海外 IP 会 **404 重定向到精选页**，拿到的是**别的视频** |
| yt-dlp 抖音提取器 | 源码不生成签名（留 TODO），2026 已失效 |

### 核心方案：页面 fetch hook 签名

抖音网页版会**全局 hook `window.fetch`**，为同源请求自动计算 `a_bogus` 签名
（页面内部能看到 `_vc_intercepted_fetch` 等钩子）。因此：

1. 用无头浏览器打开 `https://www.douyin.com/`（首页即可，**无需目标视频页可访问**，
   海外 IP 也未登录也能建立签名环境）
2. 在**页面上下文**中 `evaluate` 执行一段 `fetch` 调用详情 API —— 签名由页面
   JS 自动补全，绕过手工计算 `a_bogus` 的难题
3. 拿到 `aweme_detail` JSON 后解析出 `play_addr` / `bit_rate` 直链与元数据

该方案的关键是**复用页面自身的签名环境**，而不是在 Python 里复刻字节码签名算法
（后者随平台更新极易失效）。

### 目标一致性校验

视频页对未登录用户可能 404 重定向到精选页（推荐流）。若直接拦截"页面媒体请求"，
会误把**其它视频**当成本次目标（实测曾抓到标题不符的推荐视频）。因此：

- 详情 API 响应必须校验 `aweme_detail.aweme_id == 目标视频 ID`
- 未匹配到目标 ID 时，**丢弃**所有媒体直链兜底，绝不误下
- 若短链被重定向到首页（服务器认为是失效链接），给出明确错误诊断

### 断点续传与多源回退

CDN 对慢速客户端可能中途断流。下载器实现了：

- **断点续传**：`.part` 临时文件 + `Range: bytes=<offset>-`；服务端不支持 Range 时自动重写
- **多源探测**：对候选 URL 逐个 HEAD 探测可用性与大小，选最大可用画质
- **官方调度优先**：优先走 `www.douyin.com/aweme/v1/play/?video_id=...`，
  它会 302 到合适的 CDN 节点（大陆用户命中国内高速节点），再回退 CDN 直链
- **重试**：单 URL 失败切换下一源，最多 3 轮 × 8 源

### 图集（图文笔记）识别与下载

抖音图文帖的 `aweme_detail` 里 `video.play_addr` 存放的是**配乐 BGM（mp3）**而非
视频，且图集无真实视频 uri。解析时：

- 用 `_is_video_like_url` 过滤掉 mp3/m4a/音乐域名/静态图/封面，避免误当视频
- 图集只保留 `images` 列表；GUI 显示"图集"标记，下载走图片分支
- 图片 URL 无扩展名，**按响应头 `Content-Type` 自动识别**源格式（多为 `image/webp`）；
  下载后**按用户设置转换**为 JPG/PNG/WEBP（GUI 设置 → 图集图片格式；
  CLI `--image-format`；默认 JPG；`keep` 保持源格式）
- 转换用 Pillow：JPG 时透明通道合成白底（避免透明变黑），高保真 quality=92

### 多路径提取链

`core/douyin_extractor.py` 按优先级依次尝试，任一路径命中并校验通过即返回：

1. **page_fetch（主）**：首页上下文 fetch 详情 API（本次核心方案）
2. **page_render（备用）**：打开目标视频页/分享页，拦截详情 API 响应（同样校验目标 ID）
3. **yt-dlp + Cookie（兜底）**：配合登录 Cookie 文件；因 yt-dlp 自身不生成签名，
   仅作最后手段

同时支持登录态增强：`--login-cookie`（Netscape 格式抖音 Cookie）与
`--cdp`（连接到已登录的 Edge/Chrome 远程调试端口，复用真实会话）。

## 项目结构

```
main.py                 GUI 入口
cli.py                  命令行入口（抖音增强）
app.py                  GUI 应用封装（CustomTkinter）
ui/main_window.py       现代桌面界面（卡片布局/主题切换/缩略图/队列）
core/
  douyin_extractor.py   抖音提取/下载引擎（多路径 + 一致性校验 + 断点续传 + 图集）
  extractor.py          通用提取器（yt-dlp + 抖音兜底）
  downloader.py         下载管理器（队列/多线程/进度）
  cookie_helper.py      Playwright Cookie 获取/导出
  batch_extractor.py    剧集批量提取
  site_rules.py         多站点规则
  search_engine.py      站点搜索
utils/helpers.py        工具函数
打开爬虫.bat            GUI 一键启动脚本（自动检查环境）
```

## 常见问题

- **提取失败，弹窗只有帮助文案**：多为链接已失效（视频删除/私密）或被判定不可用；
  详情 API 未返回目标数据时会显示具体诊断
- **需要登录的视频**：先用浏览器登录抖音，导出 `douyin.com` 的 Cookie 后通过
  `--login-cookie` 或 GUI 的"导入 Cookie"
- **海外 IP 受限**：抖音对境外 IP 会限制视频页（403/404），加 `--proxy` 走国内代理
- **双击 `.py` 闪退**：`.py` 需 Python 解释器执行，请双击 `.bat` 或命令行运行

## 声明

本项目主要技术栈：`yt-dlp`（通用站提取）、`playwright`（浏览器自动化/签名环境）、
`customtkinter`（现代 GUI）、`requests`（下载）。仅供学习交流，请遵守版权与平台规则。

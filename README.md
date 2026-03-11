# Copper News Bot

每天自动抓取 [mining.com](https://www.mining.com/commodity/copper/) 的铜矿新闻，翻译成中文，发送到 Telegram 频道。运行在 GitHub Actions，无需本地电脑。

## 一次性配置步骤

### 1. 在 GitHub 创建新仓库

在 https://github.com/new 创建一个新的**私有**仓库（推荐 Private，因为包含 Bot Token）。

### 2. 上传代码

将本文件夹的所有文件推送到刚创建的仓库：

```bash
cd copper-news-bot
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/你的用户名/仓库名.git
git push -u origin main
```

### 3. 添加 Secrets

在 GitHub 仓库页面：**Settings → Secrets and variables → Actions → New repository secret**

添加两个 secret：

| Name | Value |
|------|-------|
| `BOT_TOKEN` | 你的 Telegram Bot Token |
| `CHANNEL_ID` | 你的 Telegram 频道 ID（如 `-1001848727769`） |

### 4. 启用 Actions（如果未自动启用）

在仓库的 **Actions** 标签页确认 workflow 已启用。

### 5. 手动测试

在 **Actions → Copper News Daily → Run workflow** 点击手动触发，验证是否正常运行。

## 运行时间

每天 **北京时间 09:00**（UTC 01:00）自动运行，发送前一天的新闻。

## 工作原理

1. 优先从 RSS Feed 获取文章（更稳定）
2. RSS 不可用时回退到 HTML 抓取
3. 使用 Google Translate（免费，无需 API Key）翻译标题
4. 通过 Telegram Bot API 发送消息

## 费用

**完全免费。** GitHub Actions 对公开和私有仓库均提供每月 2000 分钟免费额度，本任务每次运行约 1 分钟。

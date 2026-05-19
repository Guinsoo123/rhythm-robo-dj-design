# Notion 同步说明

## 自动同步

本仓库参考 `guinsoo-kaoyan-notes` 的方式同步文档：所有设计文档使用 Markdown 编写，push 到 `main` 分支后，GitHub Actions 会运行 `.github/workflows/sync-notion.yml`，调用 `scripts/sync_notion.py` 将仓库内 Markdown 文件同步到 Notion。

触发条件：

- push 到 `main` 分支。
- 变更了任意 `*.md` 文件。
- 变更了 `scripts/sync_notion.py`。
- 变更了 `.github/workflows/sync-notion.yml`。
- 手动运行 workflow dispatch。

## GitHub Secrets 配置

不要把 Notion token 写入仓库。需要在 GitHub 仓库页面配置 Secrets：

1. 打开 GitHub 仓库页面。
2. 进入 `Settings -> Secrets and variables -> Actions`。
3. 点击 `New repository secret`。
4. 添加 `NOTION_TOKEN`，值为 Notion integration token。
5. 添加 `NOTION_PARENT_PAGE_ID`，值为 Notion 父页面 ID 或完整页面 URL。
6. 在 Notion 父页面右上角 `Share`，把对应 integration 加入权限。

## 一键本地同步（推荐，无需 push GitHub）

Notion 父页面：[https://www.notion.so/364e7deff2df806bbdfef25534d88078](https://www.notion.so/364e7deff2df806bbdfef25534d88078)

```bash
cd /path/to/rhythm-robo-dj-design

# 首次配置
cp .env.local.example .env.local
# 编辑 .env.local：填入 NOTION_TOKEN（Integration secret）

# 同步
chmod +x scripts/sync_notion_local.sh   # 仅需一次
./scripts/sync_notion_local.sh
```

脚本会读取 `.env.local`（已加入 `.gitignore`），调用 `scripts/sync_notion.py` 将全部 Markdown 同步为 Notion 子页面；` ```latex ` 围栏会渲染为公式块。

可选参数：

| 参数 | 说明 |
| --- | --- |
| `--test-markdown` | 校验公式解析，不访问 Notion |
| `--dry-run` | 统计各文件的块数量，不访问 Notion |

## 本地手动同步（等价于上述脚本核心）

```bash
export NOTION_TOKEN="ntn_example"
export NOTION_PARENT_PAGE_ID="https://www.notion.so/364e7deff2df806bbdfef25534d88078"
python3 scripts/sync_notion.py
```

## 页面命名规则

同步脚本会把 Markdown 路径转换成 Notion 页面标题：

```text
README.md -> Rhythm Robo DJ 设计总览
02-算法设计/强化学习舞蹈控制.md -> 02-算法设计 / 强化学习舞蹈控制
03-软件架构/MuJoCo与Conda仿真平台.md -> 03-软件架构 / MuJoCo与Conda仿真平台
```

同步脚本会在页面顶部写入 `github-md-sync` 标记。后续如果仓库删除某个 Markdown 文件，脚本会归档由本脚本创建过、但当前仓库已不存在的同步页面。它不会处理没有同步标记的手工页面。

## Markdown 编写约定

为保证 Notion 同步效果稳定，建议：

- 使用一级到三级标题。
- 使用普通段落、表格、无序列表、任务列表和代码块。
- **块级公式**：使用 ` ```latex ` 围栏（或 `tex` / `math`），同步为 Notion **Equation** 块；多行用 `\\` 换行。
- **行内符号**：优先写 `$c_t$`、`$\pi$`、`$\mathcal{C}$`；旧写法 `` `pi` `` 也会自动转为行内公式（不再显示灰底代码样式）。
- 普通代码仍用 ` ```python `、` ```text ` 等围栏；**不要用反引号包裹数学变量**。
- **Notion 红色波浪线**：多为浏览器/Notion 英文拼写检查误报中文。可在浏览器关闭拼写检查，或关闭 Grammarly 等对 notion.so 的扩展。
- 图片和复杂 Mermaid 图可以在 GitHub 中查看；Mermaid 在 Notion 中仍为代码块。
- 内部链接尽量使用相对路径，例如 `[算法设计](../02-算法设计/强化学习舞蹈控制.md)`。

本地检查公式解析（不调用 Notion API）：

```bash
python3 scripts/sync_notion.py --test-markdown
```

## 常见报错

### Missing required environment variable: NOTION_TOKEN

没有配置 `NOTION_TOKEN`。在 GitHub Actions 中检查 repository secret；本地运行时检查当前终端是否 `export NOTION_TOKEN`。

### Missing required environment variable: NOTION_PARENT_PAGE_ID

没有配置 Notion 父页面 ID。它可以是 32 位页面 ID，也可以是完整 Notion 页面 URL。

### Notion API 401 或 403

Token 错误，或父页面没有分享给 integration。

### Notion API 404

页面 ID 错误，或该页面不在 integration 可访问的 workspace。

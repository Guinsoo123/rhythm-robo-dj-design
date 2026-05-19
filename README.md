# Rhythm Robo DJ 设计总览

本仓库是“使用宇树四足/人形机器人实现随摇滚音乐摇头、晃脑、蹦跳”的算法原理与软件架构设计文档。目标不是简单播放预设动作，而是让机器人能从音乐节拍、力度和段落中提取运动意图，再通过强化学习训练出的控制策略，在 MuJoCo 仿真中稳定、可控、带节奏地完成摇滚风格动作。

## 文档结构

| 目录 | 内容 |
| --- | --- |
| [01-项目总览 / 目标与边界](01-项目总览/目标与边界.md) | 项目目标、用户体验、硬件边界、验收标准 |
| [02-算法设计 / 强化学习舞蹈控制](02-算法设计/强化学习舞蹈控制.md) | 算法架构、音乐特征、状态动作奖励、训练流程和 sim-to-real |
| [02-算法设计 / 摇滚机器人跳舞原理](02-算法设计/README_摇滚机器人跳舞原理.md) | 从头到尾的原理与公式：物理、音乐相位、MDP、PPO、奖励塑形、部署链路 |
| [03-软件架构 / MuJoCo与Conda仿真平台](03-软件架构/MuJoCo与Conda仿真平台.md) | 基于 MuJoCo 和 Conda 的模块划分、目录建议、训练/评估/部署链路 |
| [04-同步与交付 / Notion同步说明](04-同步与交付/Notion同步说明.md) | push main 后自动同步 Markdown 到 Notion 的配置说明 |
| [05-路线图 / 研发里程碑](05-路线图/研发里程碑.md) | 从节拍仿真 demo 到真机小幅动作验证的阶段计划 |

## 系统一句话架构

```text
摇滚音乐 -> 音频特征提取 -> 节拍/段落/强弱条件信号 -> 强化学习策略 -> MuJoCo 机器人仿真 -> 安全门控 -> 宇树机器人真机执行
```

## 核心设计原则

| 原则 | 说明 |
| --- | --- |
| 安全优先 | 先在 MuJoCo 中证明稳定性，再逐步缩小真机动作幅度；任何音乐驱动信号都不能直接绕过姿态、关节、足端安全约束。 |
| 节奏驱动 | 动作不是随机摆动，而是围绕 beat、downbeat、onset energy 和音乐段落建立相位。 |
| 新手可理解 | 强化学习部分从“状态、动作、奖励、策略”讲起，避免只堆公式。 |
| 仿真先行 | 训练、评估、异常动作筛查全部在 MuJoCo 完成；真机只运行经过筛选的策略和低风险参数。 |
| 可同步交付 | 所有设计文档使用 Markdown 编写，push 到 `main` 后由 GitHub Actions 自动同步到 Notion。 |

## 推荐阅读顺序

1. 先读 [目标与边界](01-项目总览/目标与边界.md)，明确机器人应该像什么、不应该做什么。
2. 读 [摇滚机器人跳舞原理](02-算法设计/README_摇滚机器人跳舞原理.md)，建立物理—音乐—RL 的完整心智模型。
3. 再读 [强化学习舞蹈控制](02-算法设计/强化学习舞蹈控制.md)，对照架构与工程配置落地实现。
4. 然后读 [MuJoCo与Conda仿真平台](03-软件架构/MuJoCo与Conda仿真平台.md)，把算法落到软件模块和运行环境。
5. 最后读 [研发里程碑](05-路线图/研发里程碑.md)，按阶段实现和验证。

## Notion 同步

设计文档可同步到 Notion 父页面：[Rhythm Robo DJ（Notion）](https://www.notion.so/364e7deff2df806bbdfef25534d88078)。

### 一键本地同步（无需 push GitHub）

在仓库根目录执行：

```bash
# 首次：从模板创建本地密钥文件（已在 .gitignore，不会提交）
cp .env.local.example .env.local
# 编辑 .env.local，填入 NOTION_TOKEN 与 NOTION_PARENT_PAGE_ID

# 一键同步全部 Markdown
./scripts/sync_notion_local.sh
```

| 命令 | 作用 |
| --- | --- |
| `./scripts/sync_notion_local.sh` | 将仓库内所有 `*.md` 同步到 Notion |
| `./scripts/sync_notion_local.sh --test-markdown` | 仅测试解析（块级/行内公式），不调用 Notion API |
| `./scripts/sync_notion_local.sh --dry-run` | 列出将解析的文件与公式块数量，不调用 Notion API |

也可不用 `.env.local`，直接在终端导出环境变量后执行同一脚本：

```bash
export NOTION_TOKEN="your_integration_token"
export NOTION_PARENT_PAGE_ID="https://www.notion.so/364e7deff2df806bbdfef25534d88078"
./scripts/sync_notion_local.sh
```

**安全提醒**：不要把 `NOTION_TOKEN` 写进 Git 或发到公开渠道；仅放在 `.env.local` 或 GitHub Actions Secrets。Notion 父页面需在 Share 中授权你的 Integration。

更完整的说明（含 GitHub Actions 自动同步）见 [Notion同步说明](04-同步与交付/Notion同步说明.md)。

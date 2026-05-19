# 摇滚机器人跳舞：原理设计（从头到尾）

本文档从第一性原理说明：一段摇滚音乐如何经过特征提取、强化学习策略与低层关节控制，最终在 MuJoCo 或宇树机器人上表现为**稳定、跟拍、有摇滚顿挫感**的全身律动。面向需要实现或评审算法的读者；与 [强化学习舞蹈控制](强化学习舞蹈控制.md) 配合阅读——后者偏工程架构与训练配置，本文偏定义、直觉与公式推导。

> **Notion 同步说明**：全文使用一级至三级标题、表格与 `latex` 代码块书写公式，便于 push 到 `main` 后由 GitHub Actions 同步到 Notion（见 [Notion同步说明](../04-同步与交付/Notion同步说明.md)）。

---

## 1. 我们要解决的根本问题

### 1.1 表面目标与数学目标

| 层面 | 描述 |
| --- | --- |
| 用户体验 | 机器人听摇滚，原地摇头、摆身、下蹲起跳感，且与鼓点/重拍对齐 |
| 数学目标 | 在约束集合内最大化长期期望回报，而非跟踪一条固定关节轨迹 |

“跳舞”不是开环轨迹播放，而是**闭环反馈控制**：每一时刻根据机身是否倾斜、脚是否打滑、音乐处于哪一拍，决定下一步关节目标。

### 1.2 为什么是“带约束的节律控制”

无约束时，最大化动作幅度几乎必然导致摔倒或关节超限。因此问题本质是：

```latex
\max_{\pi} \; \mathbb{E}\left[ \sum_{t=0}^{T} \gamma^t r(s_t,a_t,s_{t+1},c_t) \right]
\quad \text{s.t.} \quad (s_t,a_t) \in \mathcal{C}
```

- `pi`：策略，即控制律。
- `c_t`：外生音乐条件（不随机器人动作改变，但随播放时间推进）。
- `C`：安全约束（关节限位、力矩、姿态、接触冲击等），通过惩罚与硬裁剪共同实现。

直觉：摇滚律动是“在站稳的前提下，让身体某些低维量（点头、摆幅、下蹲）与音乐相位锁相”。

---

## 2. 物理世界：机器人、状态与接触

### 2.1 广义坐标与状态

腿足/四足机器人在仿真中常用广义坐标 `q`（含浮动基座位置姿态与关节角）。速度为 `qdot`。机身姿态可用旋转矩阵 `R` 或四元数表示。

| 符号 | 含义 | 直觉 |
| --- | --- | --- |
| `q` | 广义位置 | 机器人“形状” |
| `qdot` | 广义速度 | 变化有多快 |
| `R` | 机身旋转 | 头朝哪、是否倾斜 |
| `omega` | 机身角速度 | 是否正在栽倒 |
| `lambda` | 接触力/接触指示 | 脚是否着地、支撑是否可靠 |

### 2.2 重力在机身坐标系中的投影

定义机身坐标系下重力方向：

```latex
g_{\mathrm{proj}} = R^\top g_0
```

其中 `g_0` 为世界系重力（例如 `[0,0,-9.81]` 方向归一化）。站立良好时，`g_proj` 接近机身 z 轴负方向；roll/pitch 变大时，`g_proj` 偏离，策略可据此恢复平衡。

这是腿足 RL 中极常用的观测：比直接给欧拉角更连续，且与“是否站直”直接相关。

### 2.3 动力学（概念层）

MuJoCo 求解形如：

```latex
M(q)\ddot{q} + b(q,\dot{q}) = \tau + J_c^\top f_c
```

- `M`：质量矩阵。
- `b`：科氏力、重力等。
- `tau`：关节力矩（控制输入经 PD 产生）。
- `f_c`：接触力，由互补约束求解。

直觉：你发给电机的不是“位置瞬间跳跃”，而是力矩；位置目标是 PD 的设定值。舞蹈动作是**在一组动态平衡流形附近**周期扰动，而不是无视动力学的几何动画。

### 2.4 低层 PD 控制

第一版采用位置残差时，低层可写为：

```latex
\tau = K_p (q^* - q) - K_d \dot{q}
```

- `q^*`：策略给出的目标（含默认站姿与残差）。
- `K_p, K_d`：刚度与阻尼，由底层或仿真配置固定。

架构上：**RL 输出慢变的目标 `q^*`，PD 负责快变力矩跟踪**。频率上 RL 50–200 Hz，PD/仿真子步更高。

---

## 3. 音乐世界：从波形到条件向量

### 3.1 时间轴与采样

设音频离散采样为 `x[n]`，采样率 `f_s`。连续时间 `t = n / f_s`。机器人控制步长 `Delta t`（仿真步）。每个控制步需要音乐条件 `c_t`，即使 beat 检测在离线完成，也按 `t` 插值查询。

### 3.2 BPM 与 beat 时间序列

BPM（beats per minute）定义节拍频率：

```latex
f_{\mathrm{beat}} = \frac{\mathrm{BPM}}{60}, \quad T_{\mathrm{beat}} = \frac{1}{f_{\mathrm{beat}}}
```

beat tracker 输出 beat 时刻序列 `{t_i^b}`。相邻 beat 间隔理想为 `T_beat`，实际歌曲有抖动，故用相位比单一“是否拍点”更鲁棒。

### 3.3 节拍相位 beat phase

设当前时刻 `t`，找到最近过去 beat `t_k^b` 与下一 beat `t_{k+1}^b`：

```latex
\phi(t) = \frac{t - t_k^b}{t_{k+1}^b - t_k^b} \in [0,1)
```

直觉：

- `phi = 0`：刚到拍点，适合点头/下蹲谷底。
- `phi = 0.5`：拍间中点，适合过渡。
- `phi -> 1`：临近下一拍。

### 3.4 相位的 sin/cos 编码

神经网络不擅长处理 0.99 与 0.01 的“接近性”。采用：

```latex
e_\phi = [\sin(2\pi\phi),\; \cos(2\pi\phi)]^\top
```

小节后相位 `phi_bar` 同理（downbeat 用于重拍强调）。

### 3.5 能量与 onset

短时能量（RMS）：

```latex
e_{\mathrm{rms}}(t) = \sqrt{ \frac{1}{W} \sum_{n \in window} x[n]^2 }
```

onset strength `o(t)` 表征新音符/鼓点出现的强度（常用谱通量或专用 onset 检测器）。直觉：鼓点响时 `o` 大，适合加大动作“顿挫”。

段落强度 `I_sec`：主歌/副歌/Break 的粗粒度标签或能量归一化曲线，使副歌动作更猛。

### 3.6 音乐条件向量

```latex
c_t = [\sin\phi,\cos\phi,\sin\phi_{\mathrm{bar}},\cos\phi_{\mathrm{bar}},
       \mathrm{bpm}_{\mathrm{norm}}, o_t, e_{\mathrm{rms},t}, I_{\mathrm{sec},t}, \rho_{\mathrm{tempo}}]^\top
```

`rho_tempo` 为节拍跟踪置信度；低置信时部署应减小 `action_scale`。

**关键原则**：`c_t` 只进入策略观测，**不**直接线性映射到关节（避免噪声直达执行器）。

---

## 4. 观测、动作与策略

### 4.1 完整观测

```latex
s_t = \mathrm{concat}\big( s_t^{\mathrm{robot}}, c_t, a_{t-1} \big)
```

```latex
s_t^{\mathrm{robot}} = [g_{\mathrm{proj}}, \omega, q, \dot{q}, \mathrm{contact}, f_c, h_{\mathrm{base}}, \ldots]
```

加入 `a_{t-1}` 可抑制相邻步动作跳变（相当于策略看到“上一帧命令”）。

### 4.2 动作：残差位置控制

```latex
a_t \in \mathbb{R}^{n_{\mathrm{joint}}}, \quad
q^*_t = q_{\mathrm{default}} + \alpha \cdot \mathrm{clip}(a_t, a_{\min}, a_{\max})
```

- `q_default`：稳定站姿，保证 `a≈0` 时机器人仍站立。
- `alpha`：幅度缩放，训练课程逐步增大，真机首测显著减小。

直觉：策略学的是“在站稳姿势上叠加摇滚律动”，而不是从零学习站立。

### 4.3 策略参数化

第一版：

```latex
\mu_t = f_\theta(s_t), \quad a_t \sim \mathcal{N}(\mu_t, \Sigma) \;\text{(训练探索)}
```

部署：`a_t = mu_t`（不加噪声），再经安全滤波。

`f_theta` 为 MLP；输入维数固定，便于调试。后续可将最近 `K` 帧 `c_{t-K:t}` 经小型 Temporal Encoder 再接入。

---

## 5. 马尔可夫决策过程（MDP）与回报

### 5.1 元组定义

```latex
\mathcal{M} = (\mathcal{S}, \mathcal{A}, P, R, \gamma)
```

- `S`：观测空间（实际实现为 `s_t` 向量）。
- `A`：残差动作空间。
- `P(s'|s,a)`：MuJoCo 一步推进；训练时对质量、摩擦、延迟等随机化。
- `R`：标量奖励，分解见下节。
- `gamma in (0,1]`：折扣因子。

音乐 `{c_t}` 可视为**外生上下文**；若 beat 与仿真时间锁定，则 `c_t = c(t)` 为确定性函数。

### 5.2 回报与价值函数

```latex
G_t = \sum_{k=0}^{\infty} \gamma^k r_{t+k}, \quad
V^\pi(s) = \mathbb{E}_\pi[G_t \mid s_t=s]
```

策略梯度思想：提高 `V^pi` 等价于提高“长期跳得好、站得稳”的期望。

---

## 6. 奖励函数：每一项的含义与公式

总奖励（与架构文档一致）：

```latex
r_t = r_{\mathrm{stab}} + r_{\mathrm{beat}} + r_{\mathrm{style}} + r_{\mathrm{energy}}
      - p_{\mathrm{safe}} - p_{\mathrm{smooth}} - p_{\mathrm{cost}}
```

### 6.1 稳定项（最高优先级）

机身 roll/pitch（或 `g_proj` 偏差）：

```latex
r_{\mathrm{upright}} = \exp\big( -k_1 (\mathrm{roll}^2 + \mathrm{pitch}^2) \big)
```

高度跟踪：

```latex
r_{\mathrm{height}} = \exp\big( -k_2 (h_{\mathrm{base}} - h_{\mathrm{target}})^2 \big)
```

摔倒惩罚：

```latex
r_{\mathrm{fall}} = \begin{cases} 1 & \text{未摔倒} \\ -C_{\mathrm{fall}} & \text{摔倒} \end{cases}
```

直觉：`exp(-误差²)` 在误差小时梯度温和，误差大时趋近 0，比线性惩罚更利于精细调节。

### 6.2 节拍对齐项

定义目标节律（以 beat 相位为自变量）：

```latex
b^*(\phi) = \sin(2\pi\phi)
```

观测低维“bob”特征 `b_t`（机身高度调制、pitch 点头等组合）：

```latex
r_{\mathrm{beat}} = \exp\big( -k_b (b_t - b^*(\phi_t))^2 \big)
```

不要对每一个关节单独锁相——高维硬约束易导致不协调与失稳。只对**少数聚合特征**锁相。

### 6.3 摇滚风格项

| 风格 | 观测量 | 直觉 |
| --- | --- | --- |
| Head bang | pitch 周期能量 | 前俯点头 |
| Sway | roll 或左右重心 | 身体摇摆 |
| Crouch bounce | `h_base` 在 downbeat 附近谷值 | 下蹲顿挫 |
| Accent | downbeat 时增大 `|b_t|` 权重 | 重拍更猛 |

可写为与参考统计量匹配的奖励，例如 pitch 频谱能量在 `f_beat` 附近集中。

### 6.4 能量匹配项

```latex
E^*_{\mathrm{motion}} = e_{\min} + g \cdot \mathrm{norm}(e_{\mathrm{rms},t})
```

```latex
E_{\mathrm{obs}} = \|\omega\| + c \|\dot{q}\|
```

```latex
r_{\mathrm{energy}} = \exp\big( -k_e (E_{\mathrm{obs}} - E^*_{\mathrm{motion}})^2 \big)
```

必须与 `p_cost`、`p_safe` 联用，否则策略可能通过疯狂加速刷分。

### 6.5 惩罚项

```latex
p_{\mathrm{smooth}} = w_a \|a_t - a_{t-1}\|^2
```

```latex
p_{\mathrm{torque}} = w_\tau \|\tau\|^2
```

```latex
p_{\mathrm{limit}} = w_l \sum_i \max(0, |q_i| - q_{i,\mathrm{soft}})^2
```

```latex
p_{\mathrm{slip}} = w_s \cdot \mathbb{1}_{\mathrm{contact}} \cdot \|v_{\mathrm{foot,tan}}\|
```

---

## 7. PPO：为什么用它以及目标函数推导

### 7.1 策略梯度回顾

目标 `J(pi) = E[G_0]`。策略梯度定理：

```latex
\nabla_\theta J = \mathbb{E}\left[ \nabla_\theta \log \pi_\theta(a|s) \cdot A_t \right]
```

`A_t` 为优势函数（动作相对平均好坏）。高方差，需 baseline 与截断。

### 7.2 优势估计（GAE）

TD 残差：

```latex
\delta_t = r_t + \gamma V(s_{t+1}) - V(s_t)
```

GAE：

```latex
\hat{A}_t = \sum_{l=0}^{\infty} (\gamma\lambda)^l \delta_{t+l}
```

`lambda in [0,1]` 在偏差与方差间折中。

### 7.3 PPO 裁剪目标

概率比：

```latex
r_t(\theta) = \frac{\pi_\theta(a_t|s_t)}{\pi_{\theta_{\mathrm{old}}}(a_t|s_t)}
```

裁剪 surrogate：

```latex
L^{\mathrm{CLIP}}(\theta) = \mathbb{E}\left[ \min\big( r_t(\theta)\hat{A}_t,\;
\mathrm{clip}(r_t(\theta), 1-\epsilon, 1+\epsilon)\hat{A}_t \big) \right]
```

直觉：若新策略使某动作概率暴增（`r_t` 很大），clip 限制更新幅度，避免一步毁掉已学会的站立。

价值损失：

```latex
L^{VF} = \mathbb{E}\left[ (V_\theta(s_t) - \hat{R}_t)^2 \right]
```

总损失：

```latex
L = -L^{\mathrm{CLIP}} + c_1 L^{VF} - c_2 \mathbb{E}[\mathrm{entropy}]
```

熵项鼓励探索，后期可衰减。

### 7.4 与舞蹈任务的对应

| PPO 机制 | 舞蹈中的意义 |
| --- | --- |
| 并行 rollout | 同时试多种 BPM/摩擦/扰动 |
| Clip | 防止“突然学会疯狂点头”毁掉平衡 |
| Value net | 估计“当前音乐段落下长期是否还能站住” |

---

## 8. 训练课程：从站稳到摇滚

分阶段启用奖励与随机化（详见 [强化学习舞蹈控制](强化学习舞蹈控制.md)）：

| 阶段 | 音乐 | 奖励重点 | 目的 |
| --- | --- | --- | --- |
| 1 | 无 | 仅稳定 | 吸引域：站立 |
| 2 | 固定 BPM 正弦相位 | 稳定 + 弱 beat | 学相位锁相 |
| 3 | 随机 BPM/强度 | + 能量匹配 | 泛化速度 |
| 4 | 真实摇滚特征 | 全项 + beat 噪声 | 抗检测误差 |
| 5 | 真实 + 风格/模仿 | + style/imitation | 观赏性 |

课程的本质是**逐步扩大可行集**，避免一开始优化景观过于崎岖。

---

## 9. 域随机化与 sim-to-real

训练时对 `P` 的随机化示例：

| 参数 | 随机范围（示例） | 直觉 |
| --- | --- | --- |
| 机身质量 | ±10% | 建模误差 |
| 关节阻尼 | ±20% | 电机/减速器 |
| 地面摩擦 | 0.5–1.2 | 不同地面 |
| 控制延迟 | 0–2 步 | 通信与驱动延迟 |
| IMU 噪声 | 高斯 | 传感器 |
| beat 时间抖动 | ±30 ms | 节拍检测误差 |

部署链：

```text
策略离线回放 -> 关节/力矩/姿态审查 -> action_scale 缩小 -> 低通滤波 -> 监护下真机 -> 逐步放大
```

sim-to-real 不是“仿真一模一样”，而是让 `pi` 在**一族相似动力学**上都可行，真机落在这一族中。

---

## 10. 端到端时间线（一次完整播放）

```text
T0  加载歌曲，离线或流式提取 {t_i^b}, BPM, onset, RMS
T1  初始化 q = q_default，s_0 送入策略
T2  每个控制步 Delta t：
      - 由 t 计算 phi, c_t
      - 读传感，构造 s_t
      - mu_t = f_theta(s_t)
      - q*_t = q_default + alpha * clip(mu_t)
      - 安全检查（限位、姿态、急停）
      - PD 计算 tau，MuJoCo/真机积分
      - （训练）算 r_t，存入 buffer
T3  歌曲结束或 episode 终止，评估 beat alignment / fall rate
T4  （训练）PPO 更新 theta
T5  （部署）导出视频与指标，人工 visual score
```

---

## 11. 评估指标（可计算定义）

| 指标 | 定义 |
| --- | --- |
| Fall rate | episode 摔倒比例 |
| Beat alignment error | 观测 bob 峰值与最近 beat 时间差的均值/方差 |
| Energy correlation | `corr(E_obs, e_rms)` |
| Torque peak | 训练/回放中 max ‖tau‖ |
| Visual score | 人工 1–5 分摇滚感 |

beat alignment 算法：

```text
1. 从 b_t 曲线找局部极大值 {t_j^peak}
2. 对每个 beat 时间 t_i^b，找最近 t_j^peak
3. 统计 |t_j^peak - t_i^b| 的均值与标准差
```

---

## 12. 符号表

| 符号 | 含义 |
| --- | --- |
| `q, qdot` | 广义位置、速度 |
| `R, omega` | 机身姿态、角速度 |
| `g_proj` | 机体系重力方向 |
| `tau` | 关节力矩 |
| `phi` | beat 相位 |
| `c_t` | 音乐条件向量 |
| `s_t` | 完整观测 |
| `a_t` | 策略输出残差 |
| `q*` | 关节目标 |
| `alpha` | action scale |
| `pi, V` | 策略与价值网络 |
| `gamma, lambda` | 折扣与 GAE 参数 |
| `epsilon` | PPO clip 范围 |

---

## 13. 常见误区（原理层）

| 误区 | 原理上为何错误 |
| --- | --- |
| 用音量直接驱动关节 | 破坏闭环稳定；噪声直达执行器 |
| 只优化 beat 不对齐稳定 | 梯度会牺牲吸引域 |
| 期望策略记忆一首歌 | 应对 `c_t` 泛化，而非波形 id |
| 省略 `a_{t-1}` | 高频抖动在电机侧放大 |
| 真机跳过安全层 | 分布外动作一次即可损坏硬件 |

---

## 14. 文档导航

| 文档 | 内容 |
| --- | --- |
| [强化学习舞蹈控制](强化学习舞蹈控制.md) | 架构、状态/动作/奖励、课程、PPO 选型、部署清单 |
| [MuJoCo与Conda仿真平台](../03-软件架构/MuJoCo与Conda仿真平台.md) | 目录、模块、训练脚本 |
| [目标与边界](../01-项目总览/目标与边界.md) | MVP 与验收 |
| [研发里程碑](../05-路线图/研发里程碑.md) | 阶段计划 |

---

## 15. 最小闭环检查清单

- [ ] 能从音频得到 `phi, e_rms, onset` 并可视化
- [ ] MuJoCo 中 `q_default` 可稳定站立
- [ ] `s_t` 维数固定，含 `g_proj, c_t, a_{t-1}`
- [ ] 奖励分项可单独 ablation
- [ ] PPO 训练 fall rate 下降
- [ ] 未见歌曲 beat alignment 可接受
- [ ] 真机路径含 scale、LPF、急停

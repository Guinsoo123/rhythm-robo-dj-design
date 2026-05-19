# 强化学习工具链与 Ubuntu 安装

本文放在软件架构文档之前，用来说明本项目需要哪些强化学习、机器人仿真和工程工具，它们分别解决什么问题，以及在 Ubuntu 22.04 上如何安装。

说明：用户常写“Ubuntu 22.4”，这里按正式版本名 **Ubuntu 22.04 LTS** 处理。GPU、CUDA、PyTorch 版本变化较快，本文给出稳定安装路径；具体 PyTorch CUDA wheel 以官方安装选择器为准。

## 工具链分层

| 层级 | 工具 | 作用 | 本项目用途 |
| --- | --- | --- | --- |
| 操作系统 | Ubuntu 22.04 LTS | 提供 Linux 驱动、编译工具和运行环境 | 训练机、仿真机、机器人开发机 |
| GPU 驱动 | NVIDIA Driver | 让 PyTorch/Isaac 使用 GPU | 加速神经网络训练和大规模仿真 |
| 环境管理 | Miniforge/Conda 或 Mamba | 固定 Python 与依赖版本 | 避免 MuJoCo、PyTorch、音频库互相污染 |
| 深度学习 | PyTorch | 神经网络、自动求导、GPU 张量计算 | actor、critic、PPO 更新 |
| 物理仿真 | MuJoCo | 多体动力学、接触、关节仿真 | 人形机器人训练与视频评估主仿真器 |
| RL 环境接口 | Gymnasium | 标准 `reset/step` API | 封装 `MujocoDanceEnv` |
| RL 算法库 | Stable-Baselines3 / CleanRL / rl-games | PPO、SAC 等算法实现 | 第一版可用 SB3 快速验证，后续自研 PPO |
| GPU 机器人仿真 | Isaac Sim / Isaac Lab | 大规模并行仿真和传感器场景 | 后续扩展，不作为第一版必需项 |
| 音频处理 | librosa / soundfile | BPM、beat、onset、RMS energy | 构造音乐条件向量 |
| 日志可视化 | TensorBoard / Weights & Biases | 训练曲线、超参、视频记录 | 调试奖励、稳定性、节奏指标 |
| 配置管理 | YAML / Hydra | 管理奖励权重、控制频率、随机化范围 | 实验可复现 |
| 机器人接口 | ROS 2 / 厂商 SDK | 真机通信、状态读取、动作发送 | sim-to-real 部署阶段 |
| 测试质量 | pytest / ruff / mypy | 单元测试、格式、类型检查 | 防止奖励和安全层回归 |

## 第一版推荐技术路线

第一版目标是做出可靠的 MuJoCo 强化学习闭环，不建议一开始引入过重工具。

```text
Ubuntu 22.04
  -> Miniforge/Conda
  -> Python 3.11
  -> PyTorch
  -> MuJoCo + Gymnasium
  -> Stable-Baselines3 PPO
  -> librosa 音频特征
  -> TensorBoard 日志
```

Isaac Lab、ROS 2、真机 SDK 放到后续阶段接入。这样能先把算法、奖励、状态动作接口和安全门控跑通。

## 系统基础安装

```bash
sudo apt update
sudo apt install -y \
  build-essential \
  git \
  curl \
  wget \
  ca-certificates \
  pkg-config \
  cmake \
  ffmpeg \
  libgl1 \
  libglfw3 \
  libglew-dev \
  libosmesa6-dev \
  libxrender1 \
  libxrandr2 \
  libxinerama1 \
  libxcursor1 \
  libxi6
```

用途说明：

| 包 | 作用 |
| --- | --- |
| `build-essential`, `cmake`, `pkg-config` | 编译 Python 扩展或机器人 SDK |
| `ffmpeg` | 导出训练视频、处理音频 |
| `libgl*`, `libglfw3`, `libosmesa6-dev` | MuJoCo 渲染和离屏渲染 |
| `git`, `curl`, `wget` | 拉取代码与安装脚本 |

## NVIDIA 驱动检查

如果使用 NVIDIA GPU：

```bash
nvidia-smi
```

能看到 GPU 型号、驱动版本和显存，说明驱动可用。若命令不存在或报错，先通过 Ubuntu “Additional Drivers” 或 NVIDIA 官方方式安装驱动。

注意：PyTorch 的 pip/conda CUDA 包通常自带运行时组件，但仍需要系统 NVIDIA 驱动。驱动版本必须支持所选 CUDA 运行时。

## 安装 Miniforge

推荐 Miniforge，因为它默认使用 conda-forge，机器人和科学计算依赖更容易统一。

```bash
cd /tmp
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3-Linux-x86_64.sh
```

安装完成后重新打开终端，或执行安装脚本提示的 shell 初始化命令。

创建环境：

```bash
conda create -n rhythm-robo-dj python=3.11 -y
conda activate rhythm-robo-dj
python -m pip install --upgrade pip wheel setuptools
```

## 安装 PyTorch

CPU 验证环境可先安装 CPU 版：

```bash
pip install torch torchvision torchaudio
```

GPU 训练环境请到 PyTorch 官方安装页选择：

```text
OS: Linux
Package: Pip 或 Conda
Language: Python
Compute Platform: 与本机驱动匹配的 CUDA 版本
```

然后复制官方生成的命令执行。安装后验证：

```bash
python - <<'PY'
import torch
print(torch.__version__)
print("cuda:", torch.cuda.is_available())
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0))
PY
```

## 安装 MuJoCo 与 Gymnasium

```bash
pip install mujoco gymnasium
```

验证 MuJoCo Python 包：

```bash
python - <<'PY'
import mujoco
print("mujoco:", mujoco.__version__)
PY
```

启动空 viewer：

```bash
python -m mujoco.viewer
```

如果服务器无桌面或无显示器，训练仍可使用离屏渲染；录制视频时需要正确配置 EGL/OSMesa。开发期建议先在有桌面环境的机器上验证模型加载和 viewer 操作。

## 安装强化学习算法库

快速原型建议先用 Stable-Baselines3：

```bash
pip install "stable-baselines3[extra]"
```

它提供 PPO、SAC、TD3、环境检查器、向量化环境和 TensorBoard 集成。第一版可用 SB3 PPO 验证奖励和环境接口；当项目需要自定义 actor-critic、音乐条件编码或更复杂 rollout 时，再迁移到自研训练循环。

可选方案：

| 工具 | 适合场景 |
| --- | --- |
| Stable-Baselines3 | 快速验证 Gymnasium 环境和 PPO baseline |
| CleanRL | 想读懂单文件算法实现，便于改 PPO 细节 |
| rl-games | 大规模 GPU 并行训练，常与 Isaac 系工具结合 |
| skrl | 机器人 RL、Isaac Lab 生态中较常见 |

## 安装音频与可视化工具

```bash
pip install librosa soundfile numpy scipy matplotlib pandas tqdm tensorboard pyyaml
```

用途：

| 工具 | 作用 |
| --- | --- |
| `librosa` | beat、onset、tempo、RMS energy |
| `soundfile` | 读取和保存音频 |
| `numpy`, `scipy` | 特征处理和信号处理 |
| `matplotlib` | 绘制奖励、节拍和动作曲线 |
| `tensorboard` | 训练曲线和视频记录 |
| `pyyaml` | 读取训练配置 |

## 推荐 environment.yml

```yaml
name: rhythm-robo-dj
channels:
  - conda-forge
dependencies:
  - python=3.11
  - pip
  - numpy
  - scipy
  - matplotlib
  - pandas
  - pyyaml
  - tqdm
  - ffmpeg
  - pip:
      - torch
      - torchvision
      - torchaudio
      - mujoco
      - gymnasium
      - "stable-baselines3[extra]"
      - librosa
      - soundfile
      - tensorboard
```

创建环境：

```bash
conda env create -f environment.yml
conda activate rhythm-robo-dj
```

GPU 环境中，建议不要直接提交写死 CUDA wheel 的 `environment.yml`。更稳的做法是在文档中记录“本机验证过的 PyTorch 安装命令”和 `nvidia-smi` 输出。

## Gymnasium 环境检查

自定义环境写好后，用 SB3 的检查器验证接口：

```python
from stable_baselines3.common.env_checker import check_env
from rhythm_robo_dj.envs.mujoco_dance_env import MujocoDanceEnv

env = MujocoDanceEnv(...)
check_env(env)
```

需要重点检查：

- `reset()` 返回 `(obs, info)`。
- `step(action)` 返回 `(obs, reward, terminated, truncated, info)`。
- observation 和 action 的 `spaces` 与实际数组形状一致。
- reward 是标量，终止逻辑区分摔倒和超时。

## Isaac Lab 可选安装位置

Isaac Lab 适合后续做大规模 GPU 并行仿真、复杂传感器和域随机化。它依赖 Isaac Sim，显存和驱动要求更高，不建议阻塞第一版 MuJoCo 路线。

建议接入时机：

| 阶段 | 是否需要 |
| --- | --- |
| MuJoCo 单机 PPO demo | 不需要 |
| 奖励和动作接口稳定后扩大并行训练 | 可评估 |
| 需要复杂视觉、深度相机或合成数据 | 推荐评估 |
| 真机部署验证 | 不直接依赖 |

安装时按 Isaac Lab 官方文档选择 pip 或源码克隆方式，并优先确认显存、驱动和 Isaac Sim 是否满足要求。

## ROS 2 与真机 SDK

ROS 2 和厂商 SDK 属于部署阶段工具，不应进入早期训练闭环。

| 工具 | 作用 | 接入建议 |
| --- | --- | --- |
| ROS 2 Humble | 节点通信、话题、日志、可视化 | Ubuntu 22.04 原生匹配，可在部署阶段接入 |
| Unitree SDK | 读取机器人状态、发送低层指令 | 先用动作回放小幅测试，再接策略 |
| rosbag | 记录真机状态与动作 | 用于分析 sim-to-real 误差 |

部署阶段必须保留：

- 动作缩放。
- 低通滤波。
- 关节限幅。
- 姿态急停。
- 手动急停。
- 策略输出记录和回放检查。

## 最小验证脚本

安装完成后执行：

```bash
python - <<'PY'
import torch
import mujoco
import gymnasium as gym
import librosa
import stable_baselines3

print("torch:", torch.__version__, "cuda:", torch.cuda.is_available())
print("mujoco:", mujoco.__version__)
print("gymnasium:", gym.__version__)
print("librosa:", librosa.__version__)
print("sb3:", stable_baselines3.__version__)
PY
```

如果这段脚本通过，说明第一版算法开发所需的 Python 工具链已经就绪。

## 常见问题

| 问题 | 原因 | 处理 |
| --- | --- | --- |
| `torch.cuda.is_available()` 为 False | PyTorch CUDA 包或驱动不匹配 | 按官方 selector 重装 PyTorch，检查 `nvidia-smi` |
| MuJoCo viewer 打不开 | 缺少 OpenGL/GLFW 或无显示器 | 安装系统图形库，服务器使用离屏渲染 |
| `librosa` 读取音频失败 | 缺少 ffmpeg 或音频格式不支持 | 安装 `ffmpeg`，先转为 wav |
| SB3 检查环境失败 | Gymnasium API 返回值不匹配 | 按 `reset/step` 新 API 修改环境 |
| 训练奖励上升但动作危险 | 奖励被策略钻空子 | 增加硬安全终止、动作限幅和评估指标 |

## 参考来源

- PyTorch 官方安装说明：https://pytorch.org/get-started/locally/
- MuJoCo Python 文档：https://mujoco.readthedocs.io/en/latest/python.html
- Stable-Baselines3 安装说明：https://stable-baselines3.readthedocs.io/
- Gymnasium / Farama 文档：https://gymnasium.farama.org/
- Isaac Lab 安装说明：https://isaac-sim.github.io/IsaacLab/

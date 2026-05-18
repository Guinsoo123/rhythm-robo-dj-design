# MuJoCo 与 Conda 仿真平台

## 技术选型

| 模块 | 选型 | 说明 |
| --- | --- | --- |
| 物理仿真 | MuJoCo | 适合机器人连续控制，接触建模稳定，Python API 成熟 |
| Python 环境 | Conda | 便于固定 Python、MuJoCo、PyTorch、音频库和可视化依赖 |
| 强化学习接口 | Gymnasium 风格环境 | 让训练、评估、视频录制和算法替换更清晰 |
| RL 算法 | PPO 优先 | 第一版稳定可靠，后续可扩展 SAC/TD3 |
| 音频处理 | librosa 或 madmom | 提取 BPM、beat、onset、RMS energy 等 |
| 配置管理 | YAML | 分离环境参数、奖励权重、训练超参和机器人型号 |
| 日志与可视化 | TensorBoard/W&B + 视频导出 | 同时观察 reward 曲线、稳定性指标和动作效果 |

## 推荐目录结构

当前仓库主要是设计文档。后续进入代码实现时，建议保持如下结构：

```text
rhythm-robo-dj-design/
  README.md
  environment.yml
  configs/
    robot_unitree_go2.yml
    train_ppo_rock_bob.yml
    reward_default.yml
  assets/
    music/
    unitree_mjcf/
  src/
    rhythm_robo_dj/
      audio/
        feature_extractor.py
        beat_tracker.py
      envs/
        mujoco_dance_env.py
        reward.py
        observations.py
        safety.py
      policies/
        actor_critic.py
        export.py
      train/
        train_ppo.py
        evaluate.py
      sim/
        viewer.py
        record_video.py
      robot/
        unitree_interface.py
        action_filter.py
        emergency_stop.py
  tests/
    test_audio_features.py
    test_reward.py
    test_safety_limits.py
  scripts/
    sync_notion.py
```

## Conda 环境设计

建议使用单独的 Conda 环境，避免 MuJoCo、PyTorch 和音频库版本互相污染。

示例 `environment.yml`：

```yaml
name: rhythm-robo-dj
channels:
  - conda-forge
  - pytorch
dependencies:
  - python=3.11
  - pip
  - numpy
  - scipy
  - pyyaml
  - matplotlib
  - tqdm
  - ffmpeg
  - pytorch
  - torchaudio
  - pip:
      - mujoco
      - gymnasium
      - librosa
      - soundfile
      - tensorboard
      - stable-baselines3
```

常用命令：

```bash
conda env create -f environment.yml
conda activate rhythm-robo-dj
python -m rhythm_robo_dj.train.train_ppo --config configs/train_ppo_rock_bob.yml
```

如果使用苹果芯片或特定 GPU，需要根据实际机器调整 PyTorch 安装方式。项目文档应记录最终验证过的 Conda 环境导出结果。

## 运行时数据流

```text
音乐文件
  -> audio.feature_extractor
  -> beat / onset / energy 特征缓存
  -> MujocoDanceEnv.reset(song_segment)
  -> policy(observation)
  -> safety.action_filter
  -> MuJoCo step
  -> reward / metrics / video
```

训练时，音频特征建议提前离线缓存。这样 RL 训练不会在每个 episode 中反复解析音频，速度更稳定。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `audio/feature_extractor.py` | 从音频文件提取 BPM、beat、onset、RMS energy、段落强度 |
| `envs/mujoco_dance_env.py` | 封装 MuJoCo 仿真、reset、step、observation、done |
| `envs/observations.py` | 组装机器人状态、音乐条件和历史动作 |
| `envs/reward.py` | 计算稳定、节拍、风格、能量、安全等奖励项 |
| `envs/safety.py` | 限制动作、判断摔倒、检测关节/速度/力矩越界 |
| `policies/actor_critic.py` | 策略网络和值函数网络 |
| `train/train_ppo.py` | 加载配置、创建并行环境、训练和保存模型 |
| `train/evaluate.py` | 使用未见过歌曲评估策略并输出指标 |
| `sim/record_video.py` | 录制 MuJoCo 视频，叠加 beat/energy 可视化 |
| `robot/action_filter.py` | 真机动作缩放、低通滤波和限幅 |
| `robot/unitree_interface.py` | 与宇树 SDK 或控制接口连接 |

## MuJoCo 环境设计

环境应尽量符合 Gymnasium API：

```python
obs, info = env.reset(seed=seed, options={"song_id": "rock_001"})
obs, reward, terminated, truncated, info = env.step(action)
```

### reset

`reset` 做这些事：

- 随机选择一首歌或一个音乐片段。
- 随机选择初始 beat phase，避免策略只记住开头。
- 重置机器人为默认站姿，加入小范围姿态扰动。
- 随机化摩擦、质量、阻尼、控制延迟等仿真参数。
- 清空上一帧动作和评估指标。

### step

`step` 做这些事：

- 接收策略动作。
- 使用动作裁剪和低通滤波。
- 转换成 MuJoCo actuator control。
- 推进若干个物理子步。
- 读取新状态并组装 observation。
- 计算 reward 和安全指标。
- 判断是否摔倒、超时或完成片段。

## 控制频率建议

| 频率 | 建议 |
| --- | --- |
| MuJoCo 物理步长 | 0.001 到 0.005 秒 |
| 策略控制频率 | 30 到 100 Hz |
| 音乐特征刷新 | 20 到 100 Hz |
| 真机指令频率 | 按宇树 SDK 推荐频率，策略输出可插值 |

音乐 beat 本身通常是 1 到 4 Hz，远低于控制频率。策略每一步都看相位，动作自然会形成连续节奏。

## 配置文件设计

训练配置建议分开写，避免把奖励权重硬编码进 Python。

示例：

```yaml
robot:
  model_xml: assets/unitree_mjcf/go2.xml
  control_mode: position_residual
  action_scale: 0.12

music:
  feature_cache: assets/music/features
  bpm_range: [80, 180]
  segment_seconds: 12

reward:
  upright: 2.0
  beat_alignment: 1.0
  energy_match: 0.5
  action_rate_penalty: 0.05
  torque_penalty: 0.001
  joint_limit_penalty: 1.0

training:
  algorithm: ppo
  num_envs: 64
  total_steps: 50000000
  seed: 42
```

## 安全门控

软件架构中必须把安全门控独立成模块，而不是散落在训练脚本里。

安全门控包括：

| 门控 | 说明 |
| --- | --- |
| action clip | 限制策略输出范围 |
| joint soft limit | 接近关节限位时缩小动作 |
| velocity limit | 限制目标变化速度 |
| low-pass filter | 平滑动作，减少高频抖动 |
| posture guard | roll/pitch 超过阈值时切回默认站姿 |
| contact guard | 接触异常或打滑时降低动作强度 |
| emergency stop | 真机异常时立即停止策略输出 |

训练环境和真机部署应复用同一套安全门控逻辑。这样训练时策略就会习惯真实部署的动作限制。

## 日志与可观测性

每次训练至少记录：

- 总 reward 和各项 reward 分量。
- 摔倒率、episode 长度、越界次数。
- 动作幅度、动作变化率、关节速度、估计力矩。
- beat alignment error 和 energy correlation。
- 随机抽样生成视频，视频中叠加 beat 竖线和能量曲线。

日志目录建议：

```text
runs/
  2026-xx-xx_ppo_go2_rock_bob/
    config.yml
    checkpoints/
    videos/
    metrics.csv
    tensorboard/
```

## 从仿真到真机

真机部署不应直接加载训练脚本，而应有单独的 runtime：

```text
音频输入/特征缓存
  -> policy runtime
  -> action filter
  -> robot safety monitor
  -> Unitree SDK
```

真机 runtime 要求：

- 没有训练依赖，不需要保存 replay buffer。
- 可以固定随机性，部署时策略输出确定。
- 支持手动设置动作强度倍率，例如 0.2、0.4、0.6。
- 支持急停和回默认站姿。
- 所有关节命令都有日志，方便复盘。

## 测试策略

| 测试 | 目的 |
| --- | --- |
| 音频特征测试 | 确保 beat、energy 缓存格式稳定 |
| reward 单元测试 | 确保关键奖励项不会因为符号写反而鼓励危险动作 |
| safety 单元测试 | 输入越界动作时必须被裁剪 |
| 环境 smoke test | reset/step 连续运行不崩溃 |
| 短训练测试 | PPO 跑少量 step，确认 loss、reward 和视频产物正常 |
| 回归评估 | 每次大改后用固定音乐集评估摔倒率和节拍误差 |

## 交付物

每个稳定版本建议交付：

- Markdown 设计文档。
- Conda 环境说明或 `environment.yml`。
- MuJoCo 模型加载说明。
- 训练配置和 reward 权重。
- 评估指标表。
- 仿真视频。
- 真机安全检查清单。

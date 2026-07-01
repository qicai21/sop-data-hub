# 工单:Qwen3.6-35B MLX-VLM greedy 路径不稳定优化

- **类型**:本地 VLM 服务稳定性优化
- **发现日期**:2026-07-01
- **状态**:已处理
- **严重度**:高

## 背景

32G Mac Studio 上使用 `Qwen3.6-35B-A3B-4bit` + `mlx-vlm 0.6.3` 作为本机唯一 VL 模型。单张图片识别能力正常,但批量评测运行到约百张后,VLM 服务在 generation thread 中报错并停止响应。

关键日志:

```text
ValueError: Thread group size (1024) is greater than the maximum allowed threads per threadgroup (896).
```

堆栈落点在 `mlx_vlm/generate/ar.py` 的 `_greedy_argmax_step()` 内部 `mx.async_eval(self._next_tokens)`。当前业务调用统一发送 `temperature: 0.0`,会触发 `mlx-vlm 0.6.3` 的 greedy fast path。

## 处理原则

- 不降低现网图片尺寸作为第一修复手段,避免损伤单据 OCR。
- 将 OpenAI 兼容 VLM 端点的零温调用提升为极低非零温度,避开 `temperature == 0` 的 greedy fast path。
- 服务启动参数另行收敛 vision cache 和默认生成长度,降低 32G 机器长期运行压力。

## 验收标准

- `/v1/chat/completions` 路径默认不再发送 `temperature: 0.0`。
- 显式传入非零温度时保持调用方设置。
- 旧 `/generate` 分支保持原行为。
- VLM 服务启动参数降低 vision feature cache 压力。

## 处理记录

代码改动:

- `src/sop_hub/vlm_client.py`
  - 新增 `OPENAI_VLM_MIN_TEMPERATURE = 0.01`。
  - 仅对 `/v1` OpenAI 兼容 VLM 端点,将 `temperature <= 0` 的请求提升到 `0.01`,避开 `mlx-vlm` 的 greedy fast path。
  - 旧 `/generate` 分支保持原 `temperature` 行为。
- `tests/test_vlm_client.py`
  - 覆盖零温 OpenAI 请求、显式非零温度、旧 `/generate` 分支三种行为。

本机服务配置:

- `/Users/qicai21/Library/LaunchAgents/com.qicai.qwen35b.mlx.plist`
  - `--vision-cache-size 2`
  - `--prefill-step-size 256`
  - `--max-tokens 768`
  - `--kv-bits 8`

验证:

```text
PYTHONPATH=src /opt/homebrew/bin/python3.14 -m pytest tests/test_vlm_client.py tests/test_classifier.py -q
13 passed
```

真实服务:

```text
GET http://127.0.0.1:8021/health
status=healthy, loaded_model=/Users/qicai21/models/Qwen3.6-35B-A3B-4bit
```

真实分类样例:

```text
图片: /Users/qicai21/Desktop/vl-model-tests/images/3293_2dd73b64e89657f807735ac9d2401dbe.jpg
结果: 其他业务图片
证据细类: 日现场工作记录表
```

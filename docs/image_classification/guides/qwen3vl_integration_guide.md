# Qwen3-VL-8B-Instruct (MLX 4-bit) 集成手册 v2.0

本文档面向 `wx-ops-agent` workspace 下的所有 Agent，说明如何启动和调用本地 VLM（视觉语言模型）服务。

---

## 一、服务基本信息

| 属性 | 值 |
| :--- | :--- |
| **模型标识** | `mlx-community/Qwen3-VL-8B-Instruct-4bit` |
| **API 地址** | `http://localhost:8018/generate` |
| **健康检查** | `http://localhost:8018/health` |
| **后端框架** | MLX-VLM (苹果芯片 Metal 原生加速) |
| **Python 版本** | Python 3.14 |
| **内存占用** | 约 6.2 GB（常驻后台，不影响系统稳定性） |
| **LaunchAgent** | `com.qicai.qwen3vl.mlx` |

---

## 二、如何启动服务

服务通过 macOS `LaunchAgent` 管理，开机自启。如果服务未运行，可手动拉起：

```bash
# 检查服务是否在线
curl -s http://localhost:8018/health | jq .

# 如果没有响应，手动启动
launchctl load /Users/qicai21/.openclaw/workspace-expert-agent/qwen3vl-mlx-service/com.qicai.qwen3vl.mlx.plist

# 重启服务（修改配置后使用）
launchctl unload /Users/qicai21/.openclaw/workspace-expert-agent/qwen3vl-mlx-service/com.qicai.qwen3vl.mlx.plist
launchctl load  /Users/qicai21/.openclaw/workspace-expert-agent/qwen3vl-mlx-service/com.qicai.qwen3vl.mlx.plist

# 查看服务日志（排查问题）
tail -f /Users/qicai21/.openclaw/workspace-expert-agent/qwen3vl-mlx-service/logs/err.log
```

> **注意**：服务启动后需约 8-10 秒完成模型加载，加载完成后 `model_loaded` 字段变为 `true`。

---

## 三、API 调用规范

### 请求格式

- **方法**：`POST`
- **URL**：`http://localhost:8018/generate`
- **Headers**：`Content-Type: application/json`

### 请求体 (JSON)

| 字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `prompt` | string | ✅ | 识别指令 |
| `image_path` | string | ✅ | 本地图片绝对路径（无需 Base64） |
| `max_tokens` | int | ❌ | 最大输出 Token 数，默认 128，建议 128-512 |
| `temperature` | float | ❌ | 采样温度，默认 0.0，OCR 任务建议 0.0 |

### 返回体 (JSON)

```json
{
  "ok": true,
  "model_id": "mlx-community/Qwen3-VL-8B-Instruct-4bit",
  "took_ms": 9078,
  "text": "识别结果文本...",
  "image_path": "/path/to/image.jpg"
}
```

---

## 四、Python 调用示例

```python
import requests

def call_vision_model(prompt: str, image_path: str, max_tokens: int = 256) -> str:
    """
    调用本地 Qwen3-VL MLX 视觉识别服务。

    Args:
        prompt: 识别指令，如"提取单据中的公司名称、日期和重量"
        image_path: 图片的绝对路径
        max_tokens: 最大返回 Token 数

    Returns:
        模型识别的文本结果
    """
    url = "http://localhost:8018/generate"
    payload = {
        "prompt": prompt,
        "image_path": image_path,
        "max_tokens": max_tokens,
        "temperature": 0.0
    }
    # 注意：模型推理耗时 4-120 秒，客户端超时需设置 180s 以上
    response = requests.post(url, json=payload, timeout=180)
    result = response.json()
    if result.get("ok"):
        return result["text"]
    raise RuntimeError(f"模型调用失败: {result}")
```

### cURL 示例

```bash
curl -X POST http://localhost:8018/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "提取这张出港计划单中的发货单位、收货单位、日期，以 JSON 格式返回",
    "image_path": "/Users/qicai21/Desktop/复杂出港计划通知单.jpg",
    "max_tokens": 256
  }' | jq .
```

---

## 五、实测性能参考

| 图片类型 | 响应时间 | 准确率 |
| :--- | :--- | :--- |
| 物流表格单据（检车单、过磅单） | ~4 - 9 秒 | 极高，关键字段100% |
| 复杂出港计划通知单（结构化提取） | ~4 秒 | 极高，支持 Markdown/JSON 输出 |
| 大尺寸实景照片（800KB+） | ~60 - 120 秒 | 高，支持场景细节描述 |

---

## 六、注意事项

1. **并发请求**：模型为单线程推理，Agent 应**串行**提交请求，避免并发导致超时。
2. **客户端超时**：请求超时需设置 **180 秒或以上**，复杂图片可能需要 100+ 秒。
3. **图片路径**：必须是**服务器本机的绝对路径**，不支持 URL 或 Base64。
4. **服务崩溃恢复**：LaunchAgent 配置了 `KeepAlive`，崩溃后会自动重启，无需人工干预。

---

## 七、相关文件路径

| 资源 | 路径 |
| :--- | :--- |
| 服务源码 | `/Users/qicai21/.openclaw/workspace-expert-agent/qwen3vl-mlx-service/server.py` |
| LaunchAgent 配置 | `/Users/qicai21/.openclaw/workspace-expert-agent/qwen3vl-mlx-service/com.qicai.qwen3vl.mlx.plist` |
| 服务日志 (stdout) | `/Users/qicai21/.openclaw/workspace-expert-agent/qwen3vl-mlx-service/logs/out.log` |
| 服务日志 (stderr) | `/Users/qicai21/.openclaw/workspace-expert-agent/qwen3vl-mlx-service/logs/err.log` |
| Python 虚拟环境 | `/Users/qicai21/.openclaw/workspace-expert-agent/.venv-mlx/` |

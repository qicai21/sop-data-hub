# sop-data-hub

运营数据枢纽 — 图像识别、数据处理与 Agent 自动化

## 1. 项目概述

本项目是锦州港生产数据链的核心处理中心，负责将微信业务工作群中的图片数据提取为结构化信息，并与 95306 铁路发运数据交叉印证。

### 核心能力

- **图片分类**：将业务群图片自动分为 14 类（检装车通知单、出港计划通知单、手写箱号表、照片类等）
- **专有识别引擎**：检装车通知单、出港计划通知单、手写箱号表、耗材统计表的结构化 OCR
- **箱号校验**：ISO 6346 标准的铁路集装箱号校验与模糊修正
- **放货批次管理**：出港计划识别结果的 SQLite 持久化、合同匹配、过磅/尾货追踪
- **CLI 工具**：统一的命令行入口，支持分类、识别、校验、导入等操作

### 技术栈

| 组件 | 技术 |
|------|------|
| VLM 推理 | Gemma-4 (26B-A4B-IT) @ `localhost:8018`, Qwen3-VL (8B) @ `localhost:8019` |
| 后端框架 | Apple MLX (mlx-lm, mlx-vlm) |
| 图像处理 | Pillow, OpenCV |
| 数据持久化 | SQLite (WAL mode) |
| Python | 3.14 (`/opt/homebrew/bin/python3.14`) |

---

## 2. 快速开始

```bash
# 创建虚拟环境
python3.14 -m venv .venv
source .venv/bin/activate

# 安装项目
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 查看 CLI 帮助
python -m sop_hub --help
```

### CLI 命令速查

```bash
# 分类图片
python -m sop_hub classify path/to/image.jpg

# 识别检装车通知单
python -m sop_hub inspect path/to/inspection.jpg -o result.json

# 识别出港计划通知单
python -m sop_hub departure path/to/departure.jpg -o result.json

# 识别手写箱号表
python -m sop_hub handwritten path/to/handwritten.jpg -o result.json

# 校验/补全箱号
python -m sop_hub fix-container 0000016

# 检查 VLM 服务状态
python -m sop_hub health

# 导入放货批次
python -m sop_hub ingest path/to/release.json
python -m sop_hub list-batches
```

---

## 3. 架构概览

```
┌─────────────────────────────────────────────┐
│              sop-data-hub                    │
│                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │ Classifier│──▶│ Pipeline │──▶│ Storage  │ │
│  │ (分类器)  │   │ (路由+处理)│  │ (归档)   │ │
│  └──────────┘   └────┬─────┘   └──────────┘ │
│                      │                        │
│         ┌────────────┼────────────┐           │
│         ▼            ▼            ▼           │
│  ┌────────────┐┌──────────┐┌──────────┐      │
│  │ Inspection ││ Departure││ Handwrit.│      │
│  │ SlipEngine ││ PlanEng. ││ ListEng. │      │
│  └────────────┘└──────────┘└──────────┘      │
│                                              │
│  ┌──────────────────────────────────────┐    │
│  │ Data Agent (放货批次 SQLite 管理)     │    │
│  └──────────────────────────────────────┘    │
│                                              │
│  ┌──────────────────────────────────────┐    │
│  │ Utils: container_fixer, layout, ...   │    │
│  └──────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
        ▲                          │
        │ HTTP API                 │ JSON/JSONL
   ┌────┴─────┐              ┌────▼─────┐
   │ Gemma-4  │              │ data/    │
   │ Qwen3-VL │              │ archive  │
   └──────────┘              └──────────┘
```

---

## 4. 图片分类体系

共 14 个类别，分 4 大桶：

### 表格类 (table)
| 类别 | 动作 | 说明 |
|------|------|------|
| 出港计划通知单 | `departure_plan_extract` | 深度结构化提取 |
| 检装车通知单 | `inspection_slip_extract` | 深度结构化提取（含继承逻辑） |
| 耗材统计表 | `materials_extract` | 深度结构化提取 |
| 请车表 | `save_only` | 仅归档 |
| 日现场工作记录表 | `save_only` | 仅归档 |

### 手写类 (handwritten)
| 类别 | 动作 |
|------|------|
| 手写箱号车号表 | `save_only` |
| 手写记录 | `save_only` |

### 照片类 (photo)
| 类别 |
|------|
| 照片-敞车内部情况和作业 |
| 照片-火车涂写mark |
| 照片-货垛 |
| 照片-集装箱内情况和作业 |
| 照片-检查工人 |
| 照片-装卸现场情况 |
| 照片-杂物垃圾-塑料布 |

### 其他
| 类别 |
|------|
| other (未识别/低置信度) |

---

## 5. 识别引擎详解

### 5.1 检装车通知单引擎 (`inspection_slip.py`)

最核心的识别引擎，560+ 行代码，实现：

- **布局分析**：自动检测 A4 横版（左右半页）与竖版
- **滑动窗口切分**：将大图切分为 16 个可叠加的 chunk，逐块 OCR
- **行数据归一化**：统一处理 `seq`、`car_type`、`car_no`、`cargo_info_raw`、`remark`、`defect`
- **继承逻辑**：基于"站名+货名"锚点自动推断空白行的 `cargo_info_effective`
- **缺陷检测**：识别"门缝"、"挡板"、"车帮"等关键词自动标记缺陷
- **对账验证**：将 OCR 行数与表头/表尾声称的车数交叉对比
- **版式适配**：支持普通版和氧化铝版（多 `tarp_no`、`piece_count` 字段）

#### 行字段规范

| 核心字段 | 说明 |
|---------|------|
| `seq` | 序号 |
| `car_type` | 车型 (P64, C64 等) |
| `car_no` | 车号 (7位) |
| `cargo_info_raw` | 原始货物信息（OCR 直出） |
| `cargo_info_effective` | 继承补全后的有效货物信息 |
| `remark` | 备注 |
| `defect` | 是否有缺陷 |

### 5.2 出港计划通知单引擎 (`departure_plan.py`)

- 提取发货单位、收货单位、船名、货名、总量等结构化字段
- 解析备注中的分批计划（日期、序号、吨数、运输方式、到站）

### 5.3 手写箱号表引擎 (`handwritten_list.py`)

- 识别手写 7 位数字
- 联动 `container_fixer` 进行 ISO 6346 校验

---

## 6. 数据存储规范

### 图片归档
```
data/images/<群名>/<yy-mm>/<分类>/xxx.jpg
```

### 结构化数据
```
data/loaded_cars/<群名>/检装车通知单/YYMMDD-seq-data.json
data/cargo_release/<群名>/出港计划通知单/YYMMDD-seq-data.json
data/materials/<群名>/耗材统计表/YYMMDD-seq-data.json
```

每个目录下同时维护 `records.jsonl` 追加式流水记录。

### `doc_detail_mode` 策略

| 模式 | 行为 |
|------|------|
| `shallow` | 分类 + 保存图片 + 写基础 JSON，不调用引擎 |
| `full` | 调用引擎进行完整结构化识别 |

---

## 7. VLM 服务接口

### 基本信息

| 服务 | 地址 | 模型 |
|------|------|------|
| Gemma-4 | `http://localhost:8018/generate` | `gemma-3-4b-it` (26B-A4B) |
| Qwen3-VL | `http://localhost:8019/generate` | `Qwen3-VL-8B-Instruct-4bit` |

### 请求格式

```python
import requests

response = requests.post("http://localhost:8018/generate", json={
    "prompt": "提取单据中的关键信息，以 JSON 格式返回",
    "image_path": "/absolute/path/to/image.jpg",
    "max_tokens": 256,
    "temperature": 0.0
}, timeout=180)

result = response.json()  # {"ok": true, "text": "...", "took_ms": 9078}
```

### 注意事项

1. **串行请求**：模型单线程推理，避免并发
2. **超时设置**：需 180 秒以上（复杂图片可达 120 秒）
3. **图片路径**：必须是服务器本机的绝对路径
4. **健康检查**：`GET /health` 返回 `model_loaded` 状态

---

## 8. 放货批次数据管理

### 数据库模型

SQLite 数据库 `data/sop_agent.db`，包含两张核心表：

- `contracts`：合同信息（甲方、乙方、到站、运输方式、价格等）
- `release_batches`：放货批次（船名、货名、批次日期、批次量、过磅状态、尾货等）

### 关键业务逻辑

- **Upsert 去重**：相同 `batch_key`（船名+货名+发/收货方+到站+日期+序号）的记录自动更新
- **合同自动匹配**：根据货名和到站模糊匹配已有合同
- **过磅继承**：同一船名的新批次自动继承上一批的过磅偏好
- **尾货计算**：`planned - actual_shipped = tail_cargo_weight`

### 字段映射

| 数据库字段 | 中文名 | 来源 |
|-----------|--------|------|
| `ship_name` | 船名 | `business_info.船名` |
| `cargo_name` | 货名 | `cargo_info.货物名称` |
| `destination_station` | 到站 | `special_matter` 解析 |
| `batch_quantity` | 批次数量 | `remarks[].plan` 解析 |
| `is_weighed` | 是否过磅 | 继承逻辑 |

---

## 9. 测试指南

```bash
# 全部测试
pytest tests/ -v

# 仅运行容器号测试（最快，纯逻辑）
pytest tests/test_container_fixer.py -v

# 仅运行数据Agent测试
pytest tests/test_data_agent.py -v
```

测试覆盖范围：
- `test_container_fixer.py` — ISO6346 校验位、前缀推断、边界值
- `test_classifier.py` — JSON 提取、分类路由、Mock API 集成
- `test_inspection_engine.py` — 行归一化、继承逻辑、缺陷检测、对账
- `test_data_agent.py` — 中文日期解析、批次 upsert、去重
- `test_pipeline.py` — 策略路由、doc_detail_mode、配置加载

---

## 10. 持续化运行设计（规划中）

未来将支持以下自动化运行模式：

1. **Watch 模式**：监听 wx-ops-agent 产出目录，新图片自动触发 pipeline
2. **定时轮询**：cron/systemd timer 定期扫描
3. **健康巡检**：定时 ping VLM 服务，异常告警
4. **断点续跑**：checkpoint 机制避免重复处理

---

## 11. 项目结构

```
sop-data-hub/
├── src/sop_hub/
│   ├── classifier/       # 图片分类器 (classifier.py, prompts.py)
│   ├── engines/          # 识别引擎 (inspection_slip, departure_plan, handwritten_list, materials_stats)
│   ├── pipeline/         # 处理流水线 (pipeline, processors, strategy, models, doc_detail_mode)
│   ├── data_agent/       # 放货批次数据管理 (agent, db, json_utils)
│   ├── storage/          # 存储 (image_store, artifact_store)
│   ├── utils/            # 工具 (container_fixer, image_utils, layout)
│   └── cli.py            # CLI 入口
├── tests/                # 60 个单元测试
├── samples/              # 测试样本 (triage, ground_truth, release_batch, images)
├── pyproject.toml        # 项目配置
├── Makefile              # install/test/clean
└── README.md             # 本文件
```

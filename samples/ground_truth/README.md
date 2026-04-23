# Image Cases

把功能测试图片放在这个目录下，每张图片配一个预期 JSON：

- 图片：`<image_name>.png` / `.jpg` / `.jpeg` / `.webp` / `.bmp`
- 预期：`<image_name>_estimate_result.json`

测试会自动：

1. 读取图片
2. 运行完整图片处理 pipeline
3. 将图片保存到临时输出目录下的 `save_images/`
4. 将结构化结果保存到临时输出目录下的 `save_data/`
5. 读取保存后的数据，并和 JSON 里的关键字段比对

推荐的 JSON 结构：

```json
{
  "input": {
    "group_name": "铁晟业务工作群",
    "wxid": "18919596289@chatroom",
    "msg_time": 1710000000
  },
  "expect": {
    "relative_image_path": "铁晟业务工作群/出港计划通知单/24-03/departruecargo.png",
    "artifact_relative_path": "铁晟业务工作群/出港计划通知单/240310-1-data.json",
    "final_category": "出港计划通知单",
    "route_action": "departure_plan_extract",
    "payload_expectations": [
      {
        "path": "header_info.通知日期",
        "equals": "2026年03月27日"
      },
      {
        "path": "cargo_info.货物名称",
        "equals": "铜精矿"
      },
      {
        "path": "remarks",
        "equals": 2
      }
    ]
  }
}
```

说明：

- `relative_image_path` 和 `artifact_relative_path` 都是相对路径，不要写绝对路径
- 测试输出默认走临时目录，不会污染仓库里的 fixtures
- 现在功能测试入口放在 [`tests/functional/test_image_recognition_flow.py`](/Users/qicai21/projects/repos/wx-ops-agent/tests/functional/test_image_recognition_flow.py)
- `payload_expectations` 里的 `path` 支持点号路径，例如 `header_info.通知日期`
- 如果要校验列表元素，可以写成 `rows[0].car_no`

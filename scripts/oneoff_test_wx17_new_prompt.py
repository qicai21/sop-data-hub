"""一次性:用新 prompt 重新跑 wx_17 那张图,看 qwen3-vl 是否还把 row[8] 错读
成"鞍子河"、是否还把 4933255 读成 493255。"""
from __future__ import annotations
import sys
sys.path.insert(0, '/Users/qicai21/projects/repos/sop-data-hub/src')

import json
from sop_hub.engines.inspection_slip import InspectionSlipEngine

IMG = "/Users/qicai21/Documents/bussiness-artifacts/wechat_images/_pending/2026-06/images/95_736fa64cbb6c00f90841fa0bef414f7b.jpg"

engine = InspectionSlipEngine(service_url="http://127.0.0.1:8021/v1/chat/completions")
res = engine.process_image(image_path=IMG)
rows = res.get("rows") or []
print(f"识别 rows = {len(rows)}")
print()
# 重点查 row 索引 8(第 9 行,车号应是 4936358)和 row 索引 28(第 29 行,车号应是 4933255)
for i in [0, 1, 2, 3, 7, 8, 9, 27, 28, 29, 34, 35]:
    if i < len(rows):
        r = rows[i]
        c = r.get("cargo_info_raw") or ""
        n = r.get("car_no") or ""
        print(f"  row[{i:2}] car_no={n!r:>10} cargo_info_raw={c!r}")
print()
# 总结:理想结果
# row[2] cargo = '鞍子河'  ← 唯一应有
# row[8] cargo = ''        ← 旧 prompt 误读"鞍子河"
# row[28].car_no = '4933255'(7 位) ← 旧 prompt 输出 '493255'(6 位)
rouge8_cargo = (rows[8].get("cargo_info_raw") or "") if len(rows) > 8 else "<no row>"
row28_car = (rows[28].get("car_no") or "") if len(rows) > 28 else "<no row>"
print(f"=== 验证关键改进点 ===")
print(f"  row[ 8] cargo_info_raw = {rouge8_cargo!r}  (应该为空)")
print(f"  row[28] car_no         = {row28_car!r}  (应该是 '4933255', 7位)")

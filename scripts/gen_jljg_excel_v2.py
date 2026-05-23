#!/usr/bin/env python3
"""Regenerate Jilin Jingang Excel (correct format: 一车一行/车号+箱号1+箱号2) + JSON preview"""
import sqlite3, json
from datetime import datetime, timedelta
from pathlib import Path
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

rail_db = '/Users/qicai21/projects/repos/rail95306-sync/runtime/95306_collection.sqlite3'
OUT_DIR = Path('/Users/qicai21/projects/repos/ops-data-hub/reports')
DESKTOP = Path('/Users/qicai21/Desktop')

now = datetime.now()
date_str = now.strftime("%Y%m%d")
time_str = now.strftime("%H%M")

conn = sqlite3.connect(rail_db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

rows = cur.execute("""
    SELECT car_no, container_no_raw, container_numbers_json, cargo_name, ticketed_at
    FROM shipments 
    WHERE cargo_name LIKE '%镍%' AND origin_name='高桥镇' AND destination_name='四平'
    AND ticketed_at >= '2026-05-21'
    ORDER BY ticketed_at
""").fetchall()
records = [dict(r) for r in rows]
conn.close()

# ── Excel (46行, 一车一行, 箱号1+箱号2不拆分) ──
filename = f"吉林金钢_发运数据_{date_str}_{time_str}.xlsx"
filepath = OUT_DIR / filename
OUT_DIR.mkdir(parents=True, exist_ok=True)

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "高桥镇装车发运明细"

header_fill = PatternFill(start_color="D9EAD3", end_color="D9EAD3", fill_type="solid")
header_font = Font(bold=True, size=10)
header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
thin_border = Border(
    left=Side(style='thin'), right=Side(style='thin'),
    top=Side(style='thin'), bottom=Side(style='thin')
)

# ── 抬头行 ──
title_row = 1
ws.merge_cells(start_row=title_row, start_column=1, end_row=title_row, end_column=14)
title_cell = ws.cell(row=title_row, column=1, value="高桥镇装车发运明细")
title_cell.font = Font(bold=True, size=14)
title_cell.alignment = Alignment(horizontal='center', vertical='center')
title_cell.fill = PatternFill(start_color="B4C6E7", end_color="B4C6E7", fill_type="solid")
for c in range(1, 15):
    ws.cell(row=title_row, column=c).border = thin_border

# ── 列头行 ──
col_header_row = 2
headers = ['序号', '供应商名称', '明细户', '车皮号', '箱号1', '箱号2',
           '装车日期', '进场日期', '货名', '船名', '入场合同号', '订单标识号',
           '原发重量', '次数']

for col, h in enumerate(headers, 1):
    cell = ws.cell(row=col_header_row, column=col, value=h)
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = header_align
    cell.border = thin_border

for i, r in enumerate(records, 1):
    cnos = json.loads(r['container_numbers_json']) if r['container_numbers_json'] else ['', '']
    box1 = cnos[0] if len(cnos) > 0 else ''
    box2 = cnos[1] if len(cnos) > 1 else ''
    
    row_data = [
        i,
        '',
        '',
        r['car_no'],
        box1,
        box2,
        (r.get('ticketed_at') or '')[:10],
        (datetime.strptime((r.get('ticketed_at') or '')[:10], '%Y-%m-%d') + timedelta(days=30)).strftime('%Y-%m-%d') if r.get('ticketed_at') else '',
        '红土镍矿',
        '长航滨海',
        'JGCG-SFY-HTNK20260501',
        'CGR20260518174420',
        '',
        '单次'
    ]
    for col, val in enumerate(row_data, 1):
        cell = ws.cell(row=col_header_row + i, column=col, value=val)
        cell.border = thin_border
        cell.alignment = Alignment(vertical='center')

# Column widths
widths = [6, 24, 22, 12, 18, 18, 14, 14, 10, 12, 26, 24, 12, 8]
for col, w in enumerate(widths, 1):
    ws.column_dimensions[chr(64+col)].width = w

wb.save(str(filepath))

# Copy to desktop
import shutil
desk_path = DESKTOP / filename
shutil.copy2(str(filepath), str(desk_path))

print(f"✅ Excel: {filename}")
print(f"   路径: {filepath}")
print(f"   桌面: {desk_path}")
print(f"   总行数: {len(records)} 行 (46车, 每车一行)")
print(f"   列: {' | '.join(headers)}")

# ── JSON 预览 (92条, 一箱一条) ──
print(f"\n━━━ JSON 工厂上传预览（前3条，共 {len(records)*2} 条）━━━")
samples = []
for r in records[:2]:
    cnos = json.loads(r['container_numbers_json']) if r['container_numbers_json'] else []
    for box in cnos:
        samples.append({
            "wagonNumber": r['car_no'],
            "boxNumber": box,
            "goodName": "红土镍矿",
            "boatName": "长航滨海",
            "contractNumber": "JGCG-SFY-HTNK20260501",
            "orderId": "CGR20260518174420",
            "numberTime": "单次",
            "formId": "MR07",
            "first": "高桥镇装车发运明细"
        })

print(json.dumps(samples[:3], ensure_ascii=False, indent=2))
print(f"\n   (共 {len(records)*2} 条: {len(records)}车 × 2箱)")
print(f"   不调用 http://111.26.178.96:88")

# Save JSON preview for reference
json_preview_path = OUT_DIR / f"jljg_factory_upload_preview_{date_str}_{time_str}.json"
with open(json_preview_path, 'w', encoding='utf-8') as f:
    json.dump({"total": len(records)*2, "preview": samples}, f, ensure_ascii=False, indent=2)
print(f"\n✅ JSON预览已保存: {json_preview_path}")

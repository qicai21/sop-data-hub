"""构建本地VLM对比测试的冻结评测集(#model-eval-test-design)。

从 message_inbox(人工校正过的 classification_label)分层抽样 → 解析真图路径 →
过滤脏行(xlsx/缺失) → 给目标类(出港计划/检装单)接下游已确认记录做 GT →
写 eval_set.jsonl(版本化冻结,两模型跑同一份)。

抽样:易混簇(检装单/日现场表/请车表/手写箱号表)+目标类 取满 --per-class;
照片/长尾按 --photo-cap 封顶。固定随机种子,可复现。

用法:python scripts/build_eval_set.py --per-class 30 --photo-cap 12 --out data/eval/eval_set.jsonl
"""
from __future__ import annotations
import argparse, json, random, sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOP_DB = REPO / "data" / "sop_agent.db"
ARTIFACTS = Path("/Users/qicai21/Documents/bussiness-artifacts/wechat_images")
IMG_EXT = (".jpg", ".jpeg", ".png")
SEED = 42


def build_basename_index() -> dict[str, str]:
    """图入库后会从群收件箱移到 business/projects/ 业务目录,message_inbox 存的是
    原始路径。文件名 seq_md5.jpg 唯一 → 按 basename 建索引,真图随便它移到哪都能找到。"""
    idx: dict[str, str] = {}
    for p in ARTIFACTS.rglob("*"):
        if p.suffix.lower() in IMG_EXT and "_vlm" not in p.name:  # 排除VLM预览缩图
            idx.setdefault(p.name, str(p))
    return idx

# 易混簇 + 目标类:取满;照片/长尾:封顶
FULL_CLASSES = {"检装车通知单", "出港计划通知单", "日现场工作记录表", "请车表", "手写箱号车号表"}
SKIP_LABELS = {"", "error", None}


def resolve_image(row, idx: dict[str, str]) -> str | None:
    for p in (row["raw_standard_image_path"], row["msg_path"], row["raw_msg_path"]):
        if not p:
            continue
        if str(p).lower().endswith(IMG_EXT):
            if Path(p).exists():
                return str(p)
            hit = idx.get(Path(p).name)  # 按 basename 找移动后的真图
            if hit:
                return hit
    return None


def downstream_gt(conn, msg_id: str, label: str, img_basename: str = "") -> dict:
    """目标类接下游已确认记录做强GT;否则退 *_result.json 弱GT。
    双链:source_message_id 或 图片basename=source_file_name。"""
    if label == "出港计划通知单":
        r = conn.execute(
            "SELECT ship_name, total_planned_quantity, contract_no, destination_station, "
            "consignor, consignee FROM release_batches "
            "WHERE source_message_id=? OR source_file_name=? LIMIT 1",
            (msg_id, img_basename)).fetchone()
        if r:
            return {"gt_source": "downstream:release_batch",
                    "fields": {"ship_name": r[0], "planned_qty": r[1], "contract_no": r[2],
                               "destination": r[3], "consignor": r[4], "consignee": r[5]}}
    if label == "检装车通知单":
        cars = [str(x[0]) for x in conn.execute(
            "SELECT DISTINCT car_no FROM wagon_shipments WHERE source_message_id=?", (msg_id,))]
        if cars:
            return {"gt_source": "downstream:wagons", "fields": {"car_count": len(cars), "car_nos": sorted(cars)}}
    return {"gt_source": "weak:result_json", "fields": None}  # harness 再去读 extraction_json_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=30, help="易混簇+目标类每类取满到此上限")
    ap.add_argument("--photo-cap", type=int, default=12, help="照片/长尾类每类封顶")
    ap.add_argument("--out", default="data/eval/eval_set.jsonl")
    a = ap.parse_args()
    rng = random.Random(SEED)

    print("建图片 basename 索引(扫 artifacts)...")
    idx = build_basename_index()
    print(f"  索引 {len(idx)} 张真图")

    conn = sqlite3.connect(str(SOP_DB)); conn.row_factory = sqlite3.Row
    by_label: dict[str, list] = {}
    for row in conn.execute(
        "SELECT message_id, classification_label, raw_standard_image_path, msg_path, "
        "raw_msg_path, extraction_json_path FROM message_inbox "
        "WHERE classification_label IS NOT NULL AND classification_label NOT IN ('','error')"):
        img = resolve_image(row, idx)
        if not img:
            continue
        by_label.setdefault(row["classification_label"], []).append(
            {"message_id": row["message_id"], "label": row["classification_label"],
             "image_path": img, "extraction_json_path": row["extraction_json_path"]})

    out_rows, stats = [], {}
    for label, items in sorted(by_label.items()):
        cap = a.per_class if label in FULL_CLASSES else a.photo_cap
        rng.shuffle(items)
        picked = items[:cap]
        for it in picked:
            gt = downstream_gt(conn, it["message_id"], label, Path(it["image_path"]).name)
            out_rows.append({**it, **gt})
        stats[label] = f"{len(picked)}/{len(items)}"

    outp = REPO / a.out
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"评测集写入 {outp}  共 {len(out_rows)} 张")
    print("各类抽样(取/总):")
    for label, s in stats.items():
        print(f"  {label}: {s}")
    strong = sum(1 for r in out_rows if r["gt_source"].startswith("downstream"))
    print(f"目标类强GT(下游确认): {strong} | 其余弱GT/纯分类")


if __name__ == "__main__":
    main()

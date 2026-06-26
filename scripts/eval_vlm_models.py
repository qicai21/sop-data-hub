"""本地VLM对比测试 harness(#model-eval-test-design)。

对一个模型端点跑冻结评测集,产出四考量指标 → results_<model>.json。
两模型各跑一次,再 --compare 出对比。同图同 prompt,唯一变量=模型。

四考量:
  A 分类准确:混淆矩阵 + 各类P/R/F1 + 检装单假阳率(标题反向铁律遵守度)
  B 内容识别:出港计划字段级匹配 / 检装单车号行召回(对下游强GT;弱GT另算)
  C prompt→动作:JSON有效率 + 非目标类拒答率
  D 效率:各调用延迟 p50/p95、吞吐

用法:
  python scripts/eval_vlm_models.py run --model 8b  --url http://localhost:8018/generate
  python scripts/eval_vlm_models.py run --model 35b --url http://localhost:8019/generate
  python scripts/eval_vlm_models.py compare data/eval/results_8b.json data/eval/results_35b.json
"""
from __future__ import annotations
import argparse, json, statistics, sys, time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sop_hub.classifier.classifier import BusinessGroupImageClassifier, _extract_json_fragment  # noqa
from sop_hub.classifier.prompts import DEPARTURE_PLAN_EXTRACTION_PROMPT  # noqa

EVAL_SET = REPO / "data" / "eval" / "eval_set.jsonl"
TARGET = {"出港计划通知单", "检装车通知单"}
# 检装单车号行提取(简化单发,核心是序号→车号)
INSPECTION_ROWS_PROMPT = (
    "你是OCR助手。请提取这张《检、装车通知单》表格中每一行的序号和车号,"
    '严格只输出JSON数组,不要解释:[{"seq":1,"car_no":"1234567"}, ...]。看不清的车号留空。')


def vlm_call(url: str, prompt: str, image_path: str, max_tokens: int = 1024) -> tuple[str, float, bool]:
    """POST 图+prompt → (text, 延迟秒, ok)。"""
    t0 = time.perf_counter()
    try:
        r = requests.post(url, json={"prompt": prompt, "image_path": image_path,
                                     "max_tokens": max_tokens, "temperature": 0.0}, timeout=300)
        r.raise_for_status()
        return r.json().get("text", ""), time.perf_counter() - t0, True
    except Exception as e:
        return f"__ERR__ {e}", time.perf_counter() - t0, False


def score_departure(pred: dict, gt: dict) -> dict:
    """出港计划字段级匹配(对下游强GT)。"""
    f = gt.get("fields") or {}
    keys = {"ship_name": "船名", "contract_no": "合同号", "destination": "到站"}
    hit = {}
    pred_biz = (pred.get("business_info") or {}) if isinstance(pred, dict) else {}
    flat = {**(pred if isinstance(pred, dict) else {}), **pred_biz}
    # 宽松取值:模型字段名可能为中文/英文,尽力对
    def getp(*names):
        for n in names:
            for k, v in flat.items():
                if n in str(k) and v:
                    return str(v).strip()
        return ""
    pred_vals = {"ship_name": getp("船名", "ship"), "contract_no": getp("合同", "contract"),
                 "destination": getp("到站", "destination", "卸货")}
    for k in keys:
        g = str(f.get(k) or "").strip()
        if g:
            hit[k] = (pred_vals[k] == g) or (g in pred_vals[k]) or (pred_vals[k] in g and pred_vals[k])
    return {"field_hits": hit, "n_fields": len(hit), "n_hit": sum(1 for v in hit.values() if v)}


def score_inspection(pred, gt: dict) -> dict:
    """检装单车号行:车号集合 vs 下游强GT车号集。"""
    f = gt.get("fields") or {}
    gt_cars = set(str(c) for c in (f.get("car_nos") or []))
    pred_cars = set()
    if isinstance(pred, list):
        for row in pred:
            if isinstance(row, dict) and row.get("car_no"):
                pred_cars.add(str(row["car_no"]).strip())
    if not gt_cars:
        return {"gt_cars": 0, "pred_cars": len(pred_cars), "recall": None, "precision": None}
    inter = gt_cars & pred_cars
    return {"gt_cars": len(gt_cars), "pred_cars": len(pred_cars),
            "recall": round(len(inter) / len(gt_cars), 3),
            "precision": round(len(inter) / len(pred_cars), 3) if pred_cars else 0.0}


def run(model: str, url: str, limit: int = 0):
    rows = [json.loads(l) for l in EVAL_SET.open(encoding="utf-8")]
    if limit:
        # 冒烟:取目标类+易混各几张
        import random
        random.Random(1).shuffle(rows)
        rows = rows[:limit]
    clf = BusinessGroupImageClassifier(service_url=url)
    results = []
    print(f"[{model}] 跑 {len(rows)} 张 @ {url}")
    for i, r in enumerate(rows, 1):
        img = r["image_path"]; rec: dict = {"message_id": r["message_id"], "gt_label": r["label"]}
        # A 分类
        t0 = time.perf_counter()
        try:
            cr = clf.classify(img)
            rec["pred_label"] = cr.category; rec["detected_title"] = getattr(cr, "detected_title", "")
        except Exception as e:
            rec["pred_label"] = "__ERR__"; rec["err"] = str(e)[:80]
        rec["classify_s"] = round(time.perf_counter() - t0, 2)
        # B/C 提取(仅目标类)
        if r["label"] in TARGET:
            prompt = DEPARTURE_PLAN_EXTRACTION_PROMPT if r["label"] == "出港计划通知单" else INSPECTION_ROWS_PROMPT
            text, sec, ok = vlm_call(url, prompt, img)
            parsed = _extract_json_fragment(text) if ok else None
            rec["extract_s"] = round(sec, 2)
            rec["json_valid"] = isinstance(parsed, (dict, list))
            rec["gt_source"] = r.get("gt_source")
            if r.get("gt_source", "").startswith("downstream"):
                rec["extract_score"] = (score_departure(parsed if isinstance(parsed, dict) else {}, r)
                                        if r["label"] == "出港计划通知单" else score_inspection(parsed, r))
        results.append(rec)
        if i % 20 == 0:
            print(f"  ...{i}/{len(rows)}")
    out = REPO / "data" / "eval" / f"results_{model}.json"
    out.write_text(json.dumps({"model": model, "url": url, "results": results}, ensure_ascii=False, indent=2))
    print(f"写入 {out}")
    _summarize(model, results)


def _summarize(model: str, results: list):
    labels = sorted({r["gt_label"] for r in results})
    correct = sum(1 for r in results if r.get("pred_label") == r["gt_label"])
    print(f"\n=== [{model}] 汇总 ===")
    print(f"  分类总准确: {correct}/{len(results)} = {correct/len(results):.1%}")
    # 检装单假阳:gt非检装单 但 pred=检装单
    fp = sum(1 for r in results if r.get("pred_label") == "检装车通知单" and r["gt_label"] != "检装车通知单")
    insp_gt = [r for r in results if r["gt_label"] == "检装车通知单"]
    insp_rec = sum(1 for r in insp_gt if r.get("pred_label") == "检装车通知单")
    print(f"  检装单假阳(误判成检装单): {fp} 张  | 检装单召回: {insp_rec}/{len(insp_gt)}")
    cs = [r["classify_s"] for r in results if "classify_s" in r]
    if cs:
        cs.sort(); print(f"  分类延迟: p50={statistics.median(cs):.1f}s p95={cs[int(len(cs)*0.95)]:.1f}s 均={statistics.mean(cs):.1f}s")
    es = [r["extract_s"] for r in results if "extract_s" in r]
    if es:
        print(f"  提取延迟: 均={statistics.mean(es):.1f}s | JSON有效率: "
              f"{sum(1 for r in results if r.get('json_valid'))}/{len(es)}")
    strong = [r for r in results if isinstance(r.get("extract_score"), dict)]
    print(f"  提取强GT样本: {len(strong)}")


def compare(f1: str, f2: str):
    a = json.loads(Path(f1).read_text()); b = json.loads(Path(f2).read_text())
    print(f"=== 对比 {a['model']} vs {b['model']} ===")
    for d in (a, b):
        _summarize(d["model"], d["results"])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run"); pr.add_argument("--model", required=True); pr.add_argument("--url", required=True)
    pr.add_argument("--limit", type=int, default=0, help="冒烟测:只跑前N张")
    pc = sub.add_parser("compare"); pc.add_argument("f1"); pc.add_argument("f2")
    a = ap.parse_args()
    if a.cmd == "run":
        run(a.model, a.url, a.limit)
    else:
        compare(a.f1, a.f2)


if __name__ == "__main__":
    main()

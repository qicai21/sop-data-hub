"""#issue-20260619:检装车通知单 标题硬锚闸。

无“检、装车通知单”标题的同版式表(日现场工作记录表/请车表)即便 VLM 误判成
检装车通知单,也要被确定性降级 other,不进检装车抽取链。
"""
from __future__ import annotations

import json
from pathlib import Path

import sop_hub.vlm_client as vlm_mod
from sop_hub.classifier.classifier import BusinessGroupImageClassifier


class _Resp:
    def __init__(self, d):
        self._d = d

    def raise_for_status(self):
        pass

    def json(self):
        return {"text": json.dumps(self._d, ensure_ascii=False)}


def _classifier(monkeypatch, vlm_out):
    monkeypatch.setattr(
        BusinessGroupImageClassifier, "_prepare_preview",
        lambda self, p: Path("/tmp/_x_vlm.jpg"),
    )
    monkeypatch.setattr(vlm_mod.requests, "post", lambda *a, **k: _Resp(vlm_out))
    return BusinessGroupImageClassifier()


def test_inspection_downgraded_without_title(monkeypatch):
    # 日报:VLM 按版式误判检装车单,但 detected_title 无标题 → 降级 other
    c = _classifier(monkeypatch, {
        "category": "检装车通知单", "confidence": 0.92,
        "detected_title": "", "evidence": "印刷表格,含到站/船名/品名列",
    })
    # 4 类收敛后:无标题降级 → 归"其他业务图片"
    assert c.classify("/tmp/whatever.jpg").category == "其他业务图片"


def test_inspection_kept_with_real_title(monkeypatch):
    # 泛指"货物"标题 = 敞车 → 检装车通知单-敞车(进抽取链)
    c = _classifier(monkeypatch, {
        "category": "检装车通知单", "confidence": 0.9,
        "detected_title": "锦州港杂码公司火运货物疏港检、装车通知单", "evidence": "",
    })
    assert c.classify("/tmp/whatever.jpg").category == "检装车通知单-敞车"


def test_inspection_title_normalized_match(monkeypatch):
    # 标题带顿号/空格,归一后仍命中 → 敞车
    c = _classifier(monkeypatch, {
        "category": "检装车通知单", "confidence": 0.8,
        "detected_title": "检 、 装车通知单",
    })
    assert c.classify("/tmp/whatever.jpg").category == "检装车通知单-敞车"


def test_tonbag_title_to_other(monkeypatch):
    # 标题含具体货名(氧化铝/铜精矿)= 吨袋 → 其他业务图片,绝不进敞车抽取链
    for goods in ("氧化铝", "铜精矿"):
        c = _classifier(monkeypatch, {
            "category": "检装车通知单", "confidence": 0.9,
            "detected_title": f"锦州港杂码公司{goods}火运疏港检、装车通知单",
        })
        assert c.classify("/tmp/whatever.jpg").category == "其他业务图片"


def test_other_category_unaffected(monkeypatch):
    # 非检装车类别 → 归"其他业务图片"(请车表属其他业务单据)
    c = _classifier(monkeypatch, {
        "category": "请车表", "confidence": 0.9, "detected_title": "",
    })
    assert c.classify("/tmp/whatever.jpg").category == "其他业务图片"

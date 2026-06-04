"""中唐特钢"补充货运信息"文字消息解析器。

模板(中唐特钢发运群 [GROUP003] 群里收到的文字):

    供方: 福建漳龙集团有限公司 (天津茂远)
    船名: 丰收散运
    货名: 纽曼粉
    港口: 锦州港
    数量: 10000
    计划号: 90260500008
    合同号: ZLZT-2026050801

业务背景(海铁联运两段船):
  铁矿粉先在青岛港由 A船(进口大船)进口 → 中唐买其中一部分 → 内贸转水到锦州港
  由 B船(到港小船)承运。**这条补充货运信息上写的是 A船**(进口段),而出港计划
  通知单上写的是 B船(到港段)。二者不一致时:
    import_ship_name <- 补充货运信息.船名  (进口大船 / A 船)
    ship_name        <- 出港通知单.船名      (到港小船 / B 船,原有字段)
  一致时只填 ship_name,import_ship_name 留空。

强标识 (任一在 = 是中唐补充货运信息):
  - **合同号** 必须有(NK):中唐合同号格式如 ZLZT- 或 HT-ZT- 前缀,但不强约束
  - 7 字段中 ≥5 个能解出来才算 status=complete。

此模块**只做解析**,不写库、不查 95306、不发消息(跟 freight_detail_extractor 一样)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sop_hub.sop.monitoring_plan_matcher import MessageEvent


# ── Regex (按"键名: 值"行解析,容差中英文标点 + 全角:)─────────────────
_RE_SUPPLIER  = re.compile(r"供\s*方[:：]\s*(.+)")
_RE_SHIP      = re.compile(r"船\s*名[:：]\s*(\S+)")
_RE_CARGO     = re.compile(r"货\s*名[:：]\s*(\S+)")
_RE_PORT      = re.compile(r"港\s*口[:：]\s*(\S+)")
_RE_QUANTITY  = re.compile(r"数\s*量[:：]\s*(\d+(?:\.\d+)?)")
_RE_PLAN_ID   = re.compile(r"计划号[:：]\s*([A-Za-z0-9\-]+)")
_RE_CONTRACT  = re.compile(r"合同号[:：]\s*([A-Za-z0-9\-]+)")


@dataclass(frozen=True)
class ZhongtangFreightSupplement:
    """解析结果。

    Fields:
      message_id / group_id / message_time / raw_text:  原始上下文。
      supplier:     供方原文(可能含子公司括号,如"福建漳龙集团有限公司 (天津茂远)")
      ship_name:    进口大船名(A 船,中唐特钢发运群里写的)
      cargo_product_name:  货物品名(如"纽曼粉"、"印粉"、"麦克粉")
      port:         进口港(如"锦州港")
      quantity_tons: 数量(吨)。 -1 if unparseable.
      plan_id:      计划号
      contract_no:  合同号
      status:       "complete" | "incomplete" | "no_match"
      project_id:   固定 "zhongtang_special_steel"(由调用方决定;此处仅占位)
    """
    message_id: str
    group_id: str
    message_time: str
    raw_text: str
    supplier: str = ""
    ship_name: str = ""
    cargo_product_name: str = ""
    port: str = ""
    quantity_tons: float = -1.0
    plan_id: str = ""
    contract_no: str = ""
    status: str = "no_match"
    project_id: str = "zhongtang_special_steel"
    source: str = "zhongtang_freight_text_extractor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "group_id": self.group_id,
            "message_time": self.message_time,
            "raw_text": self.raw_text,
            "supplier": self.supplier,
            "ship_name": self.ship_name,
            "cargo_product_name": self.cargo_product_name,
            "port": self.port,
            "quantity_tons": self.quantity_tons,
            "plan_id": self.plan_id,
            "contract_no": self.contract_no,
            "status": self.status,
            "project_id": self.project_id,
            "source": self.source,
        }


_NO_MATCH = ZhongtangFreightSupplement(
    message_id="", group_id="", message_time="", raw_text="",
    status="no_match",
)


def extract_zhongtang_freight_supplement(
    event_or_text: MessageEvent | str,
    *,
    group_id: str = "",
    message_id: str = "",
    message_time: str = "",
) -> ZhongtangFreightSupplement:
    """从中唐特钢发运群的文字消息解析 7 字段。

    判定:解出 ≥5 字段 → complete;解出 1-4 字段 → incomplete;0 → no_match。
    合同号 + 计划号 至少有一个,否则视为 no_match(没法定位 release_batch)。
    """
    if isinstance(event_or_text, MessageEvent):
        raw = event_or_text.text or ""
        group_id = event_or_text.group_id or group_id
        message_id = event_or_text.message_id
        message_time = event_or_text.received_at or ""
    else:
        raw = event_or_text or ""

    if not raw.strip():
        return _NO_MATCH

    # 单字段提取
    def _grab(rx: re.Pattern[str]) -> str:
        m = rx.search(raw)
        return (m.group(1) or "").strip() if m else ""

    supplier = _grab(_RE_SUPPLIER).rstrip().rstrip("。")  # 行尾可能有句号
    ship = _grab(_RE_SHIP)
    cargo = _grab(_RE_CARGO)
    port = _grab(_RE_PORT)
    qty_s = _grab(_RE_QUANTITY)
    plan_id = _grab(_RE_PLAN_ID)
    contract = _grab(_RE_CONTRACT)

    try:
        qty = float(qty_s) if qty_s else -1.0
    except ValueError:
        qty = -1.0

    fields_present = sum(bool(x) for x in (supplier, ship, cargo, port,
                                            qty_s, plan_id, contract))
    # 合同号或计划号至少一个 — 否则无锚点回查 release_batch
    if not (plan_id or contract):
        return _NO_MATCH

    if fields_present >= 5:
        status = "complete"
    elif fields_present >= 1:
        status = "incomplete"
    else:
        status = "no_match"

    return ZhongtangFreightSupplement(
        message_id=message_id,
        group_id=group_id,
        message_time=message_time,
        raw_text=raw,
        supplier=supplier,
        ship_name=ship,
        cargo_product_name=cargo,
        port=port,
        quantity_tons=qty,
        plan_id=plan_id,
        contract_no=contract,
        status=status,
    )

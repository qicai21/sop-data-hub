# scripts/archive/

已跑完、不再复用的一次性脚本(backfill / oneoff / 救火)。归档于 2026-06-15,
保留作历史与回填配方参考,**不在日常流程里跑**。

新的手工补车 / backfill 一律走统一入库口 `sop_hub.sop.wagon_ingest.ingest_wagons`
(保证 canonical 列集 + cargo_count + 落库后重算装车重量),不要再手搓 INSERT。

| 脚本 | 干了什么 | 时间 |
|---|---|---|
| `backfill_aoniya_yundalin.py` | 奥尼亚/云大麟回填 | 2026-02 |
| `backfill_wangda97_keen.py` | 旺大97/兴回填 | 2026-02 |
| `backfill_zhongtang_remaining.py` | 中唐余量补录 | — |
| `backfill_siping_20260613.py` | 四平 wx876 37 车回填 | 2026-06-13 |
| `oneoff_merge_wx17_anzihe36.py` | wx17+鞍子河36车合并+OCR fix | — |
| `lanqi_lot06_50cars_ingest.py` | 蓝鳍 lot06 50 车导入 | — |
| `r73_chaoyang_candidate_to_wagons.py` | 朝阳 candidate→wagons(宝腾海救火源) | — |

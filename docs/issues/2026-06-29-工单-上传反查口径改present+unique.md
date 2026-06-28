# 工单:上传后反查口径错误(整批对齐)→ 改为 per-event「present + unique」

- **类型**:功能缺陷(上传后反查判定口径错误,致已成功的发运被判失败、流程不闭环)
- **发现日期**:2026-06-29
- **发现会话**:数据运维(只读排查 → 用户授权改这一处)
- **处理归属**:本会话(用户授权)
- **严重度**:中高 —— 对外动作(Excel/上传)实际已成功,但反查误判失败 → 任务 failed、审计停在 planned、链路反复 retry;长期让发运流程无法正常闭环
- **状态**:已修(分支 `fix/verify-per-event-uniqueness`,未合并,待 review)

## 现象

吉林金钢 马兰希望 lot02:2026-06-28 23:25 群消息「煤六 54节 四平铁 马兰希望」触发发运链(任务 #442)。用户反映这批没走完后续("生成并发 Excel""上传收货人系统"),微信里也没看到数据单。

实测 #442 的 output_json / 执行预览:
- ④发运 Excel 生成成功(54 行)
- ⑤工厂上传 `login_success=true, payloads=108, success=108, failure=0`(54 车 × 2 箱 = 108 箱全部成功)
- ⑤b 反查 `verified=true, total_match=false, boxes_ok=false, api_total=875`
- ⑥发送 `sent=true`
- 但 `task_status=failed`、`error="verify(per-event): some boxes missing;"`、三条 external_action_log(generate_shipping_excel / factory_upload_submit / send_shipping_excel_wechat)停在 `planned`,从未 `executed`。

## 排查过程与根因

1. **批次状态正确**:`release_batches.dispatch_status=loading` 是对的——这批还在装货(吉林 lifecycle=`full_track_to_received`)。不是"卡在 loading"。
2. **对外动作其实都做了**:Excel/上传/发送在链路里无条件执行(`run_departure_executor_chain` 无 dry/apply 开关,verify 只事后记录、不门控发送——见 `2026-06-27-发车识别` 工单"控制边界点1")。"停在 planned"是审计收尾被 error 挡住的副作用,不代表没做。
3. **真因 = 反查判定口径错误**。
   - 门户 `/transportOrder/list` 按 `orderId`(计划号 `CGR20260612094028`)查,**必然返回整单全量累计**(`api_total=875`),而本次事件 expected 只有 108 箱。
   - `2026-06-27` 整改(commit `db188ba` 非 plan / `b120719` plan 模式)**只把 expected 收窄到本次箱**,但布尔闸仍用 `factory_verify.all_boxes_found = (missing==0 AND extra==0 AND total_match)`——把旧"整单对齐"口径带了回来。
   - 于是 `total_match=(108==875)=false`、`extra≈767≠0` → `all_boxes_found=false` **恒成立**,与本次 108 箱是否真上传成功无关。`error` 文案"some boxes missing"也具误导性(真实 missing 很可能为 0,触发 false 的是 extra/total)。
4. **业务约束佐证整批对齐不可取**:用户明确——收货人系统不稳定,正确上传的数据在对方收货后**偶尔会消失**;且按计划号查必返整单全量。所以"反查要求整批 total/extra 对齐"在设计上既不可达、也不该作判据。

## 用户确立的新口径(present + unique)

上传后反查只校验**本次上传**的记录:
1. **present**:本次每个键都出现在门户返回里(`missing == 0`)→ 说明上传成功。
2. **unique**:本次每个键在返回里各自仅一条(`count == 1`)→ 新记录唯一。

二者皆满足即通过。**不再要求** `total == expected`,**不再因** `extra != 0`(门户历史/他批记录)判失败;`total`/`extra` 仍计算但仅作观测/脏 log。

键按项目:**吉林金钢 = box_no(箱号)**;**朝钢 = car_no(车号)**。

## 本次改动(分支 fix/verify-per-event-uniqueness)

1. `src/sop_hub/sop/factory_verify.py`(吉林)
   - 新增纯函数 `evaluate_presence_and_uniqueness(expected_keys, api_key_counts) -> (missing, duplicate)`。
   - 分页抓取 `all_boxes: set` → `api_box_counts: Counter`(记次数,供唯一性判定)。
   - `VerifySummary` 增 `duplicate_boxes`;`all_boxes_found` 由 `(missing==0 AND extra==0 AND total_match)` 改为 **`(missing==0 AND duplicate==0)`**;`total_match`/`extra_boxes` 保留为观测。
2. `src/sop_hub/sop/executor_runner.py`(plan + 非 plan 两条分支)
   - 累计 `missing`/`duplicate`;门控走新语义的 `all_boxes_found`;**修正误导文案**:按 `missing=` / `duplicate=` 如实区分,不再一律写 "some boxes missing"。
3. `src/sop_hub/external/chaoyang_ansteel/upload_wagons.py`(朝钢,按 car_no 对齐)
   - 抽出纯函数 `reconcile_uploaded_cars(uploaded_car_nos, site_wagons, today) -> (verified, missing, duplicate, extra)`,显式加 car_no 唯一性(原仅 set 比对,漏检重复)。
   - `UploadResult` 增 `duplicate_car_nos`;`success = (missing==0 AND duplicate==0 AND verified==uploaded)`。
4. `config/project_sops/jilin_jingang.yaml`(L569+ `verify.check`)
   - 旧 `total == expected_count / all boxNumber values match` → 新口径文案:`every uploaded box_no present (missing==0)` + `every uploaded box_no unique (count==1)`,加 `key: box_no`,与代码判定一致。

## 决策记录(用户已批准)

1. **yaml 同步**新口径文案(A)。
2. **朝钢加 car_no 查重**,与吉林对齐(B)。
3. **生效需重启 daemon**:执行发运链的是 `text-watch --run-chains` 长驻进程,改代码后必须 kickstart 重启才加载新逻辑(见 `2026-06-27-发车识别` 缺陷D)。
4. **#442 善后已授权**:#442 的 Excel/上传实际已成功(108/108),新口径下重跑会通过;三条对外动作均有幂等键,重跑不重复发/传。具体重跑/清理由用户在主机执行。

## 验证

新增单测,均覆盖三场景并通过:
- `tests/test_factory_verify_per_event.py`(吉林,box_no):A 订单有历史累计(extra=873、total≠expected)但本次箱全在且唯一→通过;B 本次缺箱→失败;C 本次箱重复→失败。
- `tests/test_chaoyang_upload_reconcile.py`(朝钢,car_no):A 有历史 extra 但本次车全在且唯一→通过;B 缺车→失败;C 重复车→失败。

sandbox 无法装 pytest(项目 `.venv` 软链指向 Mac 路径、pip 网络受限),已用标准库 `unittest.mock` 跑等效断言全 PASS;三个改动文件 `py_compile`、yaml `safe_load` 均通过。Mac 上复跑:`PYTHONPATH=src pytest tests/test_factory_verify_per_event.py tests/test_chaoyang_upload_reconcile.py`。

## 关联

- `2026-06-27-工单-发车识别与SOP触发不稳定.md`(待办3/6 expected 收窄、控制边界点1)
- `2026-06-28-工单-朝钢链入库后未走鞍钢上传反查走不完整流程.md`(两个 dispatch_status 区分、真假完成机制)
- `docs/business-rules/朝阳上传必须紧跟反查.md`(上传必反查铁律)

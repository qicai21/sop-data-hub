from pathlib import Path

from ops_hub.models.project_sop import TrackingTask, load_all_tracking_tasks, load_project_sop

WORKSPACE_ROOT = Path(__file__).resolve().parents[2].parent
FIXTURES_DIR = WORKSPACE_ROOT / "business-system-docs" / "test-plan" / "fixtures" / "project_sops"


def _find_task(tasks, *, project_id: str, group_id: str) -> TrackingTask:
    return next(t for t in tasks if t.project_id == project_id and t.group_id == group_id)


def test_loader_reads_all_project_sops():
    """测试 1: SOP loader 能同时读取中唐与朝阳基线"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    project_ids = {t.project_id for t in tasks}
    assert "zt_steel_baseline" in project_ids
    assert "chaoyang_steel_baseline" in project_ids
    assert "simple_data_archive" in project_ids
    assert "longxia_test_sandbox" in project_ids

    zt_task = _find_task(tasks, project_id="zt_steel_baseline", group_id="GROUP003")
    assert zt_task.listen_options["image"] is True
    assert zt_task.listen_options["text"] is True
    assert any(r.message_type == "text" for r in zt_task.routing)
    assert len(zt_task.routing) == 2


def test_same_group_can_be_reused_by_multiple_projects():
    """测试 2: 同一个群 [GROUP001] 可以被多个项目复用，且路由结果不同"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    group001_tasks = [t for t in tasks if t.group_id == "GROUP001"]
    project_ids = {t.project_id for t in group001_tasks}

    assert {"zt_steel_baseline", "chaoyang_steel_baseline"}.issubset(project_ids)

    zt_task = _find_task(tasks, project_id="zt_steel_baseline", group_id="GROUP001")
    cy_task = _find_task(tasks, project_id="chaoyang_steel_baseline", group_id="GROUP001")

    assert len(zt_task.routing) == 1
    assert len(cy_task.routing) == 2
    assert zt_task.routing[0].target_node == "process_inspection_slip"
    assert cy_task.routing[0].target_node == "create_release_batch"
    assert cy_task.routing[1].target_node == "process_inspection_slip"


def test_chaoyang_baseline_has_no_text_release_chain():
    """测试 3: 朝阳钢铁不包含文字放货链路，也不沿用中唐报送对象"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    cy_task = _find_task(tasks, project_id="chaoyang_steel_baseline", group_id="GROUP001")
    assert cy_task.listen_options.get("text", False) is False
    assert cy_task.listen_options.get("image", False) is True

    assert all(r.message_type != "text" for r in cy_task.routing)

    release_route = next(r for r in cy_task.routing if r.target_node == "create_release_batch")
    inspection_route = next(r for r in cy_task.routing if r.target_node == "process_inspection_slip")

    assert release_route.trigger_condition == "category_in:[出港计划通知单]"
    assert inspection_route.report_targets["dev"]["type"] == "contact"
    assert inspection_route.report_targets["dev"]["name"] == "郭东北"
    assert inspection_route.report_targets["production"]["type"] == "group"
    assert inspection_route.report_targets["production"]["group_id"] == "[待确认]"
    assert inspection_route.report_targets["production"]["group_name"] == "朝钢铁矿发运群"
    assert inspection_route.report_targets["production"]["group_id"] != "[GROUP003]"
    assert inspection_route.report_artifact["type"] == "departure_report"
    assert inspection_route.report_artifact["template_path"] == "/Users/qicai21/projects/repos/ops-data-hub/config/report_templates/cysteel_departure_report_template.xlsx"
    assert inspection_route.report_artifact["filename_pattern"] == "锦州港铁矿发运表_{wagon_count}_{date}.xlsx"
    assert inspection_route.report_artifact["generate_after"] == "departure_records_committed"
    assert inspection_route.report_artifact["send_after_generate"] is True


def test_chaogang_report_group_is_not_a_listening_source():
    """测试 3.1: 朝钢铁矿发运群只作生产报送对象，不进入监听任务"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    cy_tasks = [t for t in tasks if t.project_id == "chaoyang_steel_baseline"]
    assert cy_tasks, "朝阳 SOP fixture 必须可加载"

    assert all(t.group_name != "朝钢铁矿发运群" for t in cy_tasks)
    assert all(t.group_id != "[待确认]" for t in cy_tasks)
    assert all(t.group_lookup_id != "[待确认]" for t in cy_tasks)


def test_chaoyang_text_with_business_keywords_has_no_sop_route():
    """测试 3.2: 文字里出现船名/计划号/合同号也不能绕过 SOP 配置入库"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)
    cy_task = _find_task(tasks, project_id="chaoyang_steel_baseline", group_id="GROUP001")

    sample_text = "船名：测试轮\n计划号：CY-001\n合同号：HT-001"
    assert "船名" in sample_text and "计划号" in sample_text and "合同号" in sample_text

    matched_text_routes = [
        r for r in cy_task.routing
        if r.message_type == "text" and r.target_node == "create_release_batch"
    ]
    assert matched_text_routes == []


def test_jiusan_soybean_initial_project_requires_manual_designation():
    """测试 3.3: 九三大豆先立项目, 放货单和检装车都不自动进入业务链路"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    project_ids = {t.project_id for t in tasks}
    assert "jiusan_soybean_baseline" in project_ids

    jiusan_task = _find_task(tasks, project_id="jiusan_soybean_baseline", group_id="GROUP013")
    assert jiusan_task.group_lookup_id == "[GROUP013]"
    assert jiusan_task.group_name == "数据单发群-[GROUP013]"
    assert jiusan_task.listen_options["image"] is True
    assert jiusan_task.listen_options["text"] is True
    assert jiusan_task.listen_options["file"] is True

    assert len(jiusan_task.routing) == 1
    route = jiusan_task.routing[0]
    assert route.message_type == "*"
    assert route.trigger_condition == "manual_designation_required"
    assert route.target_node == "archive_for_manual_release_setup"
    assert route.save_db is True

    automatic_targets = {r.target_node for r in jiusan_task.routing}
    assert "create_release_batch" not in automatic_targets
    assert "process_inspection_slip" not in automatic_targets


def test_jiusan_soybean_keeps_rail95306_and_contract_metadata():
    """测试 3.4: 九三大豆的 xts 同步和合同路线配置不能被 loader 丢弃"""
    sop = load_project_sop(FIXTURES_DIR / "jiusan_soybean_baseline.yaml")

    assert sop.rail95306["account"] == "xts"
    assert sop.rail95306["cargo_name"] == "大豆"
    assert sop.rail95306["origin_station"] == "高桥镇"
    assert sop.rail95306["primary_routes"][0]["private_line"] == "三三〇处专用线"
    assert sop.rail95306["primary_routes"][1]["wagon_alias"] == "K车"
    assert sop.rail95306["supplemental_routes"][0]["destination_station"] == "得胜台"

    assert sop.contract["contract_no"] == "JGWL-JZTS-DD-202601"
    assert sop.contract["cargo_name"] == "大豆"
    assert len(sop.contract["pricing_routes"]) == 3


def test_new_group_integration_via_config():
    """测试 4: 数据单发群-[GROUP013] 是中唐补充来源，通用归档只能兜底"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    group013_tasks = [t for t in tasks if t.group_id == "GROUP013"]
    project_ids = {t.project_id for t in group013_tasks}

    assert {"zt_steel_baseline", "simple_data_archive"}.issubset(project_ids)

    zt_task = _find_task(tasks, project_id="zt_steel_baseline", group_id="GROUP013")
    assert zt_task.group_lookup_id == "[GROUP013]"
    assert zt_task.group_name == "数据单发群-[GROUP013]"
    assert zt_task.listen_options["image"] is True
    assert zt_task.listen_options["text"] is True
    assert zt_task.listen_options["file"] is True
    assert zt_task.pull_image is True

    routes = {(r.message_type, r.trigger_condition, r.target_node) for r in zt_task.routing}
    assert ("text", "match_text_template", "create_release_batch") in routes
    assert ("image", "category_in:[出港计划通知单]", "create_release_batch") in routes
    assert ("image", "category_in:[检装车通知单]", "process_inspection_slip") in routes

    inspection_route = next(r for r in zt_task.routing if r.target_node == "process_inspection_slip")
    assert inspection_route.report_targets["dev"]["type"] == "contact"
    assert inspection_route.report_targets["dev"]["name"] == "郭东北"
    assert inspection_route.report_targets["production"]["group_id"] == "[GROUP003]"

    archive_task = _find_task(tasks, project_id="simple_data_archive", group_id="GROUP013")
    assert archive_task is not None
    assert archive_task.routing[0].target_node == "archive_raw_data"
    assert archive_task.routing[0].save_db is True


def test_longxia_test_group_integration_via_project_sop():
    """测试 4.1: 龙虾测试群通过 ProjectSOP 权威配置进入监听任务。"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    longxia_task = _find_task(tasks, project_id="longxia_test_sandbox", group_id="GROUP102")
    assert longxia_task.group_lookup_id == "[GROUP102]"
    assert longxia_task.group_name == "龙虾测试群"
    assert longxia_task.listen_options["image"] is True
    assert longxia_task.pull_image is True
    assert longxia_task.routing[0].target_node == "process_business_image_test_sandbox"


def test_zt_report_targets_are_environment_specific():
    """测试 5: 中唐特钢报告目标按开发/生产环境区分"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    group001_task = _find_task(tasks, project_id="zt_steel_baseline", group_id="GROUP001")
    inspection_route = next(
        r for r in group001_task.routing
        if r.target_node == "process_inspection_slip"
    )

    assert inspection_route.report_targets["dev"]["type"] == "contact"
    assert inspection_route.report_targets["dev"]["name"] == "郭东北"
    assert inspection_route.report_targets["production"]["type"] == "group"
    assert inspection_route.report_targets["production"]["group_id"] == "[GROUP003]"


def test_zt_group_lookup_ids_keep_brackets():
    """测试 6: 微信检索标识必须保留方括号"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    group001_task = _find_task(tasks, project_id="zt_steel_baseline", group_id="GROUP001")
    group003_task = _find_task(tasks, project_id="zt_steel_baseline", group_id="GROUP003")
    group013_task = _find_task(tasks, project_id="zt_steel_baseline", group_id="GROUP013")

    assert group001_task.group_lookup_id == "[GROUP001]"
    assert group003_task.group_lookup_id == "[GROUP003]"
    assert group013_task.group_lookup_id == "[GROUP013]"


def test_agent_consumes_tasks_without_business_rules():
    """测试 7: wx-ops-agent 不持有业务规则，只消费任务输入
       验证：agent 的路由引擎只需要合并 listen_options，不需要知道具体的 target_node
    """
    tasks = load_all_tracking_tasks(FIXTURES_DIR)

    # 模拟 agent 侧对 GROUP003 的监听选项合并
    group003_tasks = [t for t in tasks if t.group_id == "GROUP003"]

    merged_listen_options = {
        "image": any(t.listen_options.get("image", False) for t in group003_tasks),
        "text": any(t.listen_options.get("text", False) for t in group003_tasks),
        "file": any(t.listen_options.get("file", False) for t in group003_tasks),
    }

    assert merged_listen_options["image"] is True
    assert merged_listen_options["text"] is True
    assert merged_listen_options["file"] is False


def test_ops_hub_is_sole_authority():
    """测试 8: 证明权威来源的切换与职责隔离
       - 证明 tracking_tasks 持有业务语义 (target_node)
       - 证明 monitored_groups 只包含派生物理指令 (listen_options)
    """
    from ops_hub.config import load_settings

    settings = load_settings()

    # 【权威层】验证 tracking_tasks
    assert hasattr(settings, "tracking_tasks")
    assert len(settings.tracking_tasks) > 0, "必须存在权威任务列表"

    zt_task = next((t for t in settings.tracking_tasks if t.project_id == "zt_steel_baseline"), None)
    cy_task = next((t for t in settings.tracking_tasks if t.project_id == "chaoyang_steel_baseline"), None)
    assert zt_task is not None
    assert cy_task is not None
    assert hasattr(zt_task, "routing")
    assert hasattr(cy_task, "routing")
    assert len(zt_task.routing) > 0
    assert len(cy_task.routing) > 0
    assert hasattr(zt_task.routing[0], "target_node")

    # 【派生输出层】验证 monitored_groups
    assert hasattr(settings, "monitored_groups")
    assert len(settings.monitored_groups) > 0, "必须生成派生输出"

    # 严格证明派生层不包含业务属性
    for group in settings.monitored_groups:
        assert "target_node" not in group, "派生输出决不能包含 target_node"
        assert "routing" not in group, "派生输出决不能包含 routing"
        assert "project_id" not in group, "派生输出决不能暴露单一 project_id (因为可能多群复用)"
        assert "listen_options" in group, "派生输出必须只包含物理层监听选项"

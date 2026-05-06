import pytest
from pathlib import Path
from ops_hub.models.project_sop import load_all_tracking_tasks, TrackingTask

FIXTURES_DIR = Path("/Users/qicai21/projects/repos/business-system-docs/test-plan/fixtures/project_sops")

def test_semantic_input_generates_standard_task_list():
    """测试 1: 语义化输入能生成正确的跟踪任务列表"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)
    
    # 验证中唐特钢群任务
    zt_task = next(t for t in tasks if t.project_id == "zt_steel_baseline" and t.group_id == "GROUP003")
    assert zt_task.listen_options["image"] is True
    assert len(zt_task.routing) == 2

def test_same_group_multiple_projects():
    """测试 2: 同一个群可以被多个项目同时使用，但路由结果不同"""
    # 这里我们在 fixture 里并没有在两个项目中配置同一个群，为了测试，我们动态模拟一个项目。
    # 实际上由于目前只加载了两个 fixture，GROUP001 只有 zt_steel_baseline 使用。
    # 但我们可以在内存中增加一个 mock 的项目，或者确认通过 list 能过滤出特定 group_id
    tasks = load_all_tracking_tasks(FIXTURES_DIR)
    
    # 提取 GROUP013 的任务
    group013_tasks = [t for t in tasks if t.group_id == "GROUP013"]
    assert len(group013_tasks) == 1
    
    # 中唐 GROUP001
    group001_tasks = [t for t in tasks if t.group_id == "GROUP001"]
    assert len(group001_tasks) == 2
    p1_task = next(t for t in group001_tasks if t.project_id == "zt_steel_baseline")
    assert p1_task.routing[0].target_node == "process_inspection_slip"
    p2_task = next(t for t in group001_tasks if t.project_id == "other_project")
    assert p2_task.routing[0].target_node == "other_business_node"

def test_new_group_integration_via_config():
    """测试 3: 新增群 数据单发群-[GROUP013] 的场景可以通过项目 SOP 配置接入"""
    tasks = load_all_tracking_tasks(FIXTURES_DIR)
    
    archive_task = next((t for t in tasks if t.group_id == "GROUP013"), None)
    assert archive_task is not None
    assert archive_task.project_id == "simple_data_archive"
    assert archive_task.routing[0].target_node == "archive_raw_data"
    assert archive_task.routing[0].save_db is True

def test_agent_consumes_tasks_without_business_rules():
    """测试 4: wx-ops-agent 不持有业务规则，只消费任务输入
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
    """测试 5: 证明权威来源的切换与职责隔离
       - 证明 tracking_tasks 持有业务语义 (target_node)
       - 证明 monitored_groups 只包含派生物理指令 (listen_options)
    """
    from ops_hub.config import load_settings
    settings = load_settings()
    
    # 【权威层】验证 tracking_tasks
    assert hasattr(settings, "tracking_tasks")
    assert len(settings.tracking_tasks) > 0, "必须存在权威任务列表"
    
    # 从中唐特钢基线抽样，证明其作为权威输入持有了业务目标
    zt_task = next((t for t in settings.tracking_tasks if t.project_id == "zt_steel_baseline"), None)
    assert zt_task is not None
    assert hasattr(zt_task, "routing")
    assert len(zt_task.routing) > 0
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

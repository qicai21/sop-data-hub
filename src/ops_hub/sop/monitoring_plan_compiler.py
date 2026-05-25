"""SOP Monitoring Plan Compiler.

Compile project-level SOP monitoring requirements into channel-level plans.
"""


class SopMonitoringPlanCompiler:
    """Compile project-level SOP monitoring requirements into channel plans."""

    def compile(self, project_sops):
        """Return channel-level monitoring plans for source agents.

        Args:
            project_sops: list of project SOP dictionaries.

        Returns:
            dict: compiled monitoring plans keyed by source channel.
        """
        if not project_sops:
            return {"wechat_monitoring_plan": {}}

        groups = {}

        for project in project_sops:
            project_id = project.get("project_id")
            for node in project.get("sop_nodes") or []:
                node_id = node.get("node_id")
                for requirement in node.get("monitoring") or []:
                    if requirement.get("channel") != "wechat":
                        continue

                    group_id = requirement.get("group_id")
                    if not group_id:
                        continue

                    group = groups.setdefault(
                        group_id,
                        {
                            "group_name": requirement.get("group_name"),
                            "watch_items": [],
                            "_item_index": {},
                        },
                    )
                    if not group.get("group_name") and requirement.get("group_name"):
                        group["group_name"] = requirement.get("group_name")

                    identity = (
                        requirement.get("input_type"),
                        requirement.get("document_type"),
                        requirement.get("message_type"),
                    )
                    item = group["_item_index"].get(identity)
                    if item is None:
                        item = {"input_type": requirement.get("input_type")}
                        if requirement.get("document_type") is not None:
                            item["document_type"] = requirement.get("document_type")
                        if requirement.get("message_type") is not None:
                            item["message_type"] = requirement.get("message_type")
                        if requirement.get("input_type") == "text":
                            item["text_patterns"] = []
                        item["candidate_projects"] = []
                        item["target_sop_nodes"] = {}
                        group["_item_index"][identity] = item
                        group["watch_items"].append(item)

                    if project_id and project_id not in item["candidate_projects"]:
                        item["candidate_projects"].append(project_id)

                    if project_id and node_id:
                        node_ids = item["target_sop_nodes"].setdefault(project_id, [])
                        if node_id not in node_ids:
                            node_ids.append(node_id)

                    if item.get("input_type") == "text":
                        for pattern in requirement.get("text_patterns") or []:
                            if pattern not in item["text_patterns"]:
                                item["text_patterns"].append(pattern)

        wechat_monitoring_plan = {}
        for group_id, group in groups.items():
            wechat_monitoring_plan[group_id] = {
                "group_name": group.get("group_name"),
                "watch_items": group["watch_items"],
            }

        return {"wechat_monitoring_plan": wechat_monitoring_plan}

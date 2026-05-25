"""SOP Monitoring Plan Compiler public contract.

The implementation is intentionally deferred. This stub exists so functional
acceptance tests can define the required behavior without failing during test
collection.
"""


class SopMonitoringPlanCompiler:
    """Compile project-level SOP monitoring requirements into channel plans."""

    def compile(self, project_sops):
        """Return channel-level monitoring plans for source agents.

        Args:
            project_sops: list of project SOP dictionaries.

        Raises:
            NotImplementedError: always in this planning branch.
        """
        raise NotImplementedError(
            "SopMonitoringPlanCompiler is not implemented in this planning branch"
        )

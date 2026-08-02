class ResourceNotFoundError(Exception):
    pass


class VersionConflictError(Exception):
    pass


class DependencyUnavailableError(Exception):
    pass


class JobDecisionUnavailableError(ValueError):
    """职位沟通决策尚未就绪，消息应延后重试而不是隔离。"""

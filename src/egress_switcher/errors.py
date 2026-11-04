class EgressError(Exception):
    """带给用户看的错误。message 用简体中文。"""


class ResolveError(EgressError):
    pass


class ApplyError(EgressError):
    pass


class CommandNotFound(EgressError):
    pass

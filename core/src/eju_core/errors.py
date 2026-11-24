class CoreError(ValueError):
    """A safe error code and recovery instruction, never a save receipt."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def result(self):
        return {"ok": False, "error": {"code": self.code, "message": str(self), **self.details}}

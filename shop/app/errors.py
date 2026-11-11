class ShopError(Exception):
    """Raised by a handler that fails. `code` is the short error name, e.g. "out_of_stock"."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

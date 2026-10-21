from dataclasses import dataclass


@dataclass(frozen=True)
class LakePaths:
    """Medallion layer locations under one lake root (a local folder or an abfss:// URI)."""

    root: str

    @property
    def bronze(self) -> str:
        return f"{self.root}/bronze"

    @property
    def silver(self) -> str:
        return f"{self.root}/silver"

    @property
    def gold(self) -> str:
        return f"{self.root}/gold"

    @property
    def reject(self) -> str:
        return f"{self.root}/reject"

    def table(self, layer: str, name: str) -> str:
        return f"{getattr(self, layer)}/{name}"

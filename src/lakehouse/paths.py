from dataclasses import dataclass

LAYERS = ("silver", "gold", "reject")


@dataclass(frozen=True)
class LakePaths:
    """Where the lake lives: Delta tables under `tables_root`, raw files under `files_root`.

    In Fabric these are a lakehouse's `Tables/` and `Files/` (abfss:// URIs or relative paths for
    Spark); `files_mount` is the local mount of `Files/` (`/lakehouse/default/Files`) for plain
    Python file APIs, which cannot open an abfss:// URI. Locally both are folders under one root.
    """

    tables_root: str
    files_root: str
    files_mount: str | None = None

    @classmethod
    def local(cls, root: str) -> "LakePaths":
        return cls(f"{root}/Tables", f"{root}/Files")

    def table(self, layer: str, name: str) -> str:
        if layer not in LAYERS:
            raise ValueError(f"unknown table layer {layer!r}; expected one of {', '.join(LAYERS)}")
        return f"{self.tables_root}/{layer}/{name}"

    @property
    def bronze(self) -> str:
        """Bronze as Spark reads it."""
        return f"{self.files_root}/bronze"

    @property
    def bronze_local(self) -> str:
        """Bronze as plain Python file APIs see it."""
        return f"{self.files_mount or self.files_root}/bronze"

    @property
    def private(self) -> str:
        """The private extracts (P1 and P2), uploaded under bronze, read by Spark."""
        return f"{self.bronze}/private"

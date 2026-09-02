from __future__ import annotations

import ast
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from .errors import ToolError

ToolCallable = Callable[[str, Mapping[str, Any]], dict[str, Any]]


class Sandbox:
    """Resolve all tool paths inside a per-Quest workspace.

    Both absolute paths and traversal outside the configured root/workspace are
    rejected. Existing symlinks are resolved before the boundary check, which
    prevents a symlink inside a workspace from exposing files outside it.
    """

    ALLOWED_WRITE_SUFFIXES: ClassVar[set[str]] = {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".py",
        ".toml",
    }

    def __init__(self, root: Path, max_file_bytes: int = 1_000_000) -> None:
        self.root = root.expanduser().resolve()
        self.max_file_bytes = max_file_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def workspace_path(self, workspace: str, *, create: bool = False) -> Path:
        if not workspace or "\x00" in workspace:
            raise ToolError("INVALID_PATH", "Workspace path is empty or invalid")
        raw = Path(workspace)
        if raw.is_absolute() or raw.drive:
            raise ToolError(
                "PATH_OUTSIDE_SANDBOX",
                "Workspace must be a relative path inside the sandbox",
                details={"workspace": workspace},
            )
        resolved = (self.root / raw).resolve(strict=False)
        if not _is_within(resolved, self.root):
            raise ToolError(
                "PATH_OUTSIDE_SANDBOX",
                "Workspace resolves outside the configured sandbox",
                details={"workspace": workspace},
            )
        if create:
            resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def resolve(
        self,
        workspace: str,
        relative_path: str,
        *,
        must_exist: bool = False,
        expect_directory: bool = False,
        create_workspace: bool = True,
    ) -> Path:
        workspace_root = self.workspace_path(workspace, create=create_workspace)
        if relative_path is None or "\x00" in str(relative_path):
            raise ToolError("INVALID_PATH", "Path is invalid")
        raw = Path(str(relative_path))
        if raw.is_absolute() or raw.drive:
            raise ToolError(
                "PATH_OUTSIDE_WORKSPACE",
                "Tool paths must be relative to the Quest workspace",
                details={"path": str(relative_path)},
            )
        candidate = (workspace_root / raw).resolve(strict=False)
        if not _is_within(candidate, workspace_root):
            raise ToolError(
                "PATH_OUTSIDE_WORKSPACE",
                "Tool path resolves outside the Quest workspace",
                details={"path": str(relative_path)},
            )
        if must_exist and not candidate.exists():
            raise ToolError(
                "PATH_NOT_FOUND",
                "Requested path does not exist",
                details={"path": str(relative_path)},
            )
        if expect_directory and candidate.exists() and not candidate.is_dir():
            raise ToolError(
                "NOT_A_DIRECTORY",
                "Requested path is not a directory",
                details={"path": str(relative_path)},
            )
        return candidate

    def display_path(self, workspace: str, path: Path) -> str:
        workspace_root = self.workspace_path(workspace)
        return path.relative_to(workspace_root).as_posix() or "."


class ToolRegistry:
    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self._tools: dict[str, ToolCallable] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, name: str, description: str, tool: ToolCallable) -> None:
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        self._tools[name] = tool
        self._descriptions[name] = description

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def describe(self) -> dict[str, str]:
        return dict(self._descriptions)

    def execute(
        self, name: str, workspace: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(
                "TOOL_NOT_FOUND",
                f"Tool '{name}' is not registered",
                details={"available_tools": list(self.names)},
            )
        if not isinstance(arguments, Mapping):
            raise ToolError(
                "INVALID_TOOL_ARGUMENTS", "Tool arguments must be an object"
            )
        return tool(workspace, arguments)


def build_default_registry(sandbox: Sandbox) -> ToolRegistry:
    registry = ToolRegistry(sandbox)
    registry.register(
        "list_directory",
        "List files and directories in one sandbox directory.",
        lambda workspace, args: _list_directory(sandbox, workspace, args),
    )
    registry.register(
        "read_file",
        "Read one UTF-8 text file inside the sandbox.",
        lambda workspace, args: _read_file(sandbox, workspace, args),
    )
    registry.register(
        "write_file",
        "Atomically write an allowed text/code file inside the sandbox.",
        lambda workspace, args: _write_file(sandbox, workspace, args),
    )
    registry.register(
        "check_markdown",
        "Check that a Markdown artifact is non-empty and has balanced fences.",
        lambda workspace, args: _check_markdown(sandbox, workspace, args),
    )
    registry.register(
        "check_python_syntax",
        "Parse Python source with ast without executing it.",
        lambda workspace, args: _check_python_syntax(sandbox, workspace, args),
    )
    return registry


def _required_string(args: Mapping[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(
            "INVALID_TOOL_ARGUMENTS",
            f"'{name}' must be a non-empty string",
            details={"argument": name},
        )
    return value


def _list_directory(
    sandbox: Sandbox, workspace: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    relative_path = args.get("path", ".")
    if not isinstance(relative_path, str):
        raise ToolError("INVALID_TOOL_ARGUMENTS", "'path' must be a string")
    directory = sandbox.resolve(
        workspace,
        relative_path,
        must_exist=False,
        expect_directory=True,
        create_workspace=False,
    )
    if not directory.exists():
        return {
            "path": sandbox.display_path(workspace, directory),
            "entries": [],
            "count": 0,
        }
    entries: list[dict[str, Any]] = []
    for item in sorted(directory.iterdir(), key=lambda value: value.name.lower()):
        # Do not follow directory symlinks here; read/write operations still do
        # their own resolved boundary check.
        entry_type = (
            "symlink" if item.is_symlink() else "directory" if item.is_dir() else "file"
        )
        entries.append(
            {
                "name": item.name,
                "path": sandbox.display_path(workspace, item),
                "type": entry_type,
                "size_bytes": item.stat(follow_symlinks=False).st_size
                if entry_type != "directory"
                else None,
            }
        )
    return {
        "path": sandbox.display_path(workspace, directory),
        "entries": entries,
        "count": len(entries),
    }


def _read_file(
    sandbox: Sandbox, workspace: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    relative_path = _required_string(args, "path")
    path = sandbox.resolve(workspace, relative_path, must_exist=True)
    if not path.is_file():
        raise ToolError(
            "NOT_A_FILE",
            "Requested path is not a file",
            details={"path": relative_path},
        )
    size = path.stat().st_size
    if size > sandbox.max_file_bytes:
        raise ToolError(
            "FILE_TOO_LARGE",
            "File exceeds the configured read limit",
            details={
                "path": relative_path,
                "size_bytes": size,
                "limit": sandbox.max_file_bytes,
            },
        )
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(
            "INVALID_TEXT_ENCODING",
            "Only UTF-8 text files can be read",
            details={"path": relative_path},
        ) from exc
    return {
        "path": sandbox.display_path(workspace, path),
        "content": content,
        "size_bytes": size,
        "line_count": len(content.splitlines()),
    }


def _write_file(
    sandbox: Sandbox, workspace: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    relative_path = _required_string(args, "path")
    content = args.get("content")
    if not isinstance(content, str):
        raise ToolError("INVALID_TOOL_ARGUMENTS", "'content' must be a string")
    path = sandbox.resolve(workspace, relative_path)
    if path.suffix.lower() not in sandbox.ALLOWED_WRITE_SUFFIXES:
        raise ToolError(
            "FILE_TYPE_NOT_ALLOWED",
            "The requested file extension is not writable by this sandbox",
            details={
                "path": relative_path,
                "allowed_extensions": sorted(sandbox.ALLOWED_WRITE_SUFFIXES),
            },
        )
    encoded = content.encode("utf-8")
    if len(encoded) > sandbox.max_file_bytes:
        raise ToolError(
            "FILE_TOO_LARGE",
            "Content exceeds the configured write limit",
            details={"size_bytes": len(encoded), "limit": sandbox.max_file_bytes},
        )
    overwrite = args.get("overwrite", True)
    if not isinstance(overwrite, bool):
        raise ToolError("INVALID_TOOL_ARGUMENTS", "'overwrite' must be a boolean")
    existed = path.exists()
    if existed and not overwrite:
        raise ToolError(
            "FILE_ALREADY_EXISTS",
            "File exists and overwrite is disabled",
            details={"path": relative_path},
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(prefix=".projecttown-", dir=path.parent)
    try:
        with os.fdopen(temp_fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return {
        "path": sandbox.display_path(workspace, path),
        "size_bytes": len(encoded),
        "created": not existed,
    }


def _check_markdown(
    sandbox: Sandbox, workspace: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    relative_path = _required_string(args, "path")
    read_result = _read_file(sandbox, workspace, {"path": relative_path})
    content = read_result["content"]
    problems: list[str] = []
    if not content.strip():
        problems.append("document is empty")
    fence_count = sum(
        1 for line in content.splitlines() if line.lstrip().startswith(("```", "~~~"))
    )
    if fence_count % 2:
        problems.append("code fence is not closed")
    if problems:
        raise ToolError(
            "MARKDOWN_INVALID",
            "Markdown validation failed",
            details={"path": relative_path, "problems": problems},
        )
    warnings = []
    if not any(line.startswith("# ") for line in content.splitlines()):
        warnings.append("document has no level-one heading")
    return {
        "path": read_result["path"],
        "valid": True,
        "line_count": read_result["line_count"],
        "warnings": warnings,
    }


def _check_python_syntax(
    sandbox: Sandbox, workspace: str, args: Mapping[str, Any]
) -> dict[str, Any]:
    relative_path = _required_string(args, "path")
    read_result = _read_file(sandbox, workspace, {"path": relative_path})
    try:
        tree = ast.parse(read_result["content"], filename=relative_path)
    except SyntaxError as exc:
        raise ToolError(
            "PYTHON_SYNTAX_INVALID",
            "Python syntax validation failed",
            details={
                "path": relative_path,
                "line": exc.lineno,
                "offset": exc.offset,
                "message": exc.msg,
            },
        ) from exc
    return {
        "path": read_result["path"],
        "valid": True,
        "line_count": read_result["line_count"],
        "functions": sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for node in ast.walk(tree)
        ),
        "classes": sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree)),
        "imports": sum(
            isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)
        ),
    }


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False

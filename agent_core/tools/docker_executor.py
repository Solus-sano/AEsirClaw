"""沙箱执行器。通过 bash 执行任意 shell 命令（含 Python、wget、ffmpeg 等）。"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from pathlib import Path

from ncatbot.utils import get_log

LOG = get_log(__name__)


class BaseExecutor(ABC):
    """沙箱执行器基类。通过 bash 执行命令。"""

    DEFAULT_TIMEOUT = 30
    MAX_OUTPUT_CHARS = 4000

    @abstractmethod
    async def execute(self, command: str, timeout: int | None = None) -> str:
        """执行 shell 命令，返回 JSON 格式结果字符串。"""

    async def ensure_ready(self) -> None:
        """确保执行环境就绪。子类可重写。"""

    async def cleanup(self) -> None:
        """清理执行环境。子类可重写。"""

    def _format_result(
        self,
        *,
        ok: bool,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        error: str = "",
    ) -> str:
        result: dict = {"ok": ok}
        if not ok and error:
            result["error"] = error
        if exit_code:
            result["exit_code"] = exit_code
        if stdout:
            result["stdout"] = stdout[: self.MAX_OUTPUT_CHARS]
            if len(stdout) > self.MAX_OUTPUT_CHARS:
                result["stdout"] += "...(truncated)"
        if stderr:
            result["stderr"] = stderr[:2000]
        return json.dumps(result, ensure_ascii=False)


class LocalExecutor(BaseExecutor):
    """本地 subprocess 执行器（bash）。用于开发阶段验证 tool_call 流程。

    **不提供沙箱隔离**，仅用于测试。生产环境应使用 DockerExecutor。
    """

    def __init__(self, workspace_dir: str = "./workspace"):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, command: str, timeout: int | None = None) -> str:
        timeout = timeout or self.DEFAULT_TIMEOUT
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command,
            cwd=str(self.workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return self._format_result(ok=False, error=f"执行超时({timeout}s)")

        return self._format_result(
            ok=proc.returncode == 0,
            exit_code=proc.returncode or 0,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
        )


class DockerExecutor(BaseExecutor):
    """Docker 沙箱执行器。管理一个长驻容器，通过 docker exec 执行 shell 命令。"""

    def __init__(
        self,
        *,
        image: str = "aesirclaw-sandbox:latest",
        container_name: str = "aesirclaw-sandbox",
        skills_dir: str = "./skills",
        workspace_dir: str = "./workspace",
        memory_limit: str = "512m",
        cpus: float = 1,
    ):
        self.image = image
        self.container_name = container_name
        self.skills_dir = Path(skills_dir).resolve()
        self.workspace_dir = Path(workspace_dir).resolve()
        self.memory_limit = memory_limit
        self.cpus = cpus

    async def ensure_ready(self) -> None:
        """确保容器正在运行。bot 启动时调用。"""
        check = await self._run_cmd(
            f"docker inspect -f '{{{{.State.Running}}}}' {self.container_name}"
        )
        if check.returncode == 0 and "true" in check.stdout:
            LOG.info("Docker 容器 %s 已在运行", self.container_name)
            return

        await self._run_cmd(f"docker rm -f {self.container_name}")
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        cmd = (
            f"docker run -d"
            f" --name {self.container_name}"
            f" --network host"
            f" -v {self.skills_dir}:/skills:ro"
            f" -v {self.workspace_dir}:/workspace"
            f" --memory={self.memory_limit}"
            f" --cpus={self.cpus}"
            f" --pids-limit=100"
            f" {self.image}"
            f" tail -f /dev/null"
        )
        result = await self._run_cmd(cmd)
        if result.returncode != 0:
            LOG.error("启动 Docker 容器失败: %s", result.stderr)
            raise RuntimeError(f"Docker 容器启动失败: {result.stderr}")
        LOG.info("Docker 容器 %s 已启动", self.container_name)

    async def execute(self, command: str, timeout: int | None = None) -> str:
        timeout = timeout or self.DEFAULT_TIMEOUT
        await self.ensure_ready()

        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i",
            "-w", "/workspace",
            self.container_name,
            "bash", "-lc", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return self._format_result(ok=False, error=f"执行超时({timeout}s)")

        return self._format_result(
            ok=proc.returncode == 0,
            exit_code=proc.returncode or 0,
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
        )

    async def cleanup(self) -> None:
        """停止并移除容器。bot 关闭时调用。"""
        await self._run_cmd(f"docker rm -f {self.container_name}")
        LOG.info("Docker 容器 %s 已清理", self.container_name)

    @staticmethod
    async def _run_cmd(cmd: str):
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()

        class Result:
            pass

        r = Result()
        r.returncode = proc.returncode
        r.stdout = stdout_b.decode(errors="replace")
        r.stderr = stderr_b.decode(errors="replace")
        return r

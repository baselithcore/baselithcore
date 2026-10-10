# ns-migrate: skip-file
"""Ship ``baselith_core_compat.pth`` at the root of site-packages.

A ``.pth`` file is the only hook that runs before user code without the user
importing anything, which is the point: a plugin written with ``from core
import …`` must load unchanged. setuptools has no declarative way to place a
file at the purelib root, so ``build_py`` copies it there.
"""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

PTH = "baselith_core_compat.pth"


class BuildPyWithPth(build_py):
    def run(self) -> None:
        super().run()
        src = Path(__file__).parent / "src" / PTH
        self.copy_file(str(src), str(Path(self.build_lib) / PTH), preserve_mode=False)

    def get_outputs(self, include_bytecode: bool = True) -> list[str]:
        return [*super().get_outputs(include_bytecode), str(Path(self.build_lib) / PTH)]


setup(cmdclass={"build_py": BuildPyWithPth})

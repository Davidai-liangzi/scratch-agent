import os
import sys


def app_dir() -> str:
    """应用所在目录：打包成 exe 后是 exe 所在目录，直接运行脚本时是项目根目录（本包的上一级）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 工程工作目录与输出文件：固定在应用目录下，保证文件生成位置可预期
WORK_DIR = os.path.join(app_dir(), "temp_project")
OUTPUT_SB3 = os.path.join(app_dir(), "my_ai_game.sb3")
CONFIG_PATH = os.path.join(app_dir(), "config.json")

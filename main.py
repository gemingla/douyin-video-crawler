#!/usr/bin/env python3
"""视频爬虫 - 通用网络视频下载工具

依赖安装：
    pip install -i https://pypi.tuna.tsinghua.edu.cn/simple yt-dlp Pillow

使用：
    python main.py
"""

import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.helpers import fix_console_encoding
fix_console_encoding()

from app import App


def main():
    try:
        app = App()
        app.run()
    except ImportError as e:
        print(f"缺少依赖：{e}")
        print("请运行: pip install -i https://pypi.tuna.tsinghua.edu.cn/simple yt-dlp Pillow")
        sys.exit(1)
    except Exception as e:
        print(f"启动失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

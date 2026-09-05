"""应用程序入口（封装）- 现代版"""

import customtkinter as ctk
from ui.main_window import MainWindow


class App:
    """视频爬虫应用程序"""

    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("视频爬虫 · Modern")
        self.main_window = MainWindow(self.root)

    def run(self):
        self.root.mainloop()

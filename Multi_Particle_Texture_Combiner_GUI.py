#!/usr/bin/env python3
"""中文图形界面入口：Multi Particle Texture Combiner。"""

import contextlib
import os
import queue
import random
import subprocess
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image
from Multi_Particle_Texture_Combiner import create_composite_atlas


FILE_TYPES = [
    ("贴图文件", "*.blp *.png"),
    ("BLP 文件", "*.blp"),
    ("PNG 文件", "*.png"),
    ("所有文件", "*.*"),
]
BLEND_MODES = ("multiply", "screen", "overlay", "add")


class QueueWriter:
    """把原脚本的 print 输出转发到界面日志。"""

    def __init__(self, events):
        self.events = events

    def write(self, text):
        if text:
            self.events.put(("log", str(text)))
        return len(text)

    def flush(self):
        pass


class TextureCombinerGUI:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.worker = None
        self.last_output = None

        root.title("多重粒子贴图合成工具")
        root.geometry("780x760")
        root.minsize(720, 700)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.mask_var = tk.StringVar()
        self.color1_var = tk.StringVar()
        self.color2_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.grid_var = tk.StringVar(value="8")
        self.cell_var = tk.StringVar(value="256")
        self.mask_grid_var = tk.StringVar(value="0")
        self.mode_var = tk.StringVar(value="multiply")
        self.blend12_var = tk.StringVar(value="multiply")
        self.variation_var = tk.BooleanVar(value=True)
        self.preview_var = tk.BooleanVar(value=False)
        self.seed_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")

        self.build_ui()

    def build_ui(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        files = ttk.LabelFrame(outer, text="贴图与输出", padding=10)
        files.grid(row=0, column=0, sticky="ew")
        files.columnconfigure(1, weight=1)

        self.add_file_row(files, 0, "遮罩贴图：", self.mask_var, self.pick_mask)
        self.add_file_row(files, 1, "颜色贴图 1：", self.color1_var,
                          lambda: self.pick_input(self.color1_var, "选择颜色贴图 1"))
        self.add_file_row(files, 2, "颜色贴图 2（可选）：", self.color2_var,
                          lambda: self.pick_input(self.color2_var, "选择颜色贴图 2"))
        ttk.Button(files, text="清除", width=8,
                   command=lambda: self.color2_var.set("")).grid(row=2, column=3, padx=(6, 0))
        self.add_file_row(files, 3, "输出 PNG：", self.output_var, self.pick_output)

        options = ttk.LabelFrame(outer, text="合成参数", padding=10)
        options.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for col in (1, 3):
            options.columnconfigure(col, weight=1)

        ttk.Label(options, text="输出网格 N×N：").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=1, to=64, textvariable=self.grid_var, width=12).grid(
            row=0, column=1, sticky="ew", padx=(6, 18))
        ttk.Label(options, text="单格尺寸（像素）：").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(options, from_=16, to=4096, increment=16,
                    textvariable=self.cell_var, width=12).grid(row=0, column=3, sticky="ew", padx=(6, 0))

        ttk.Label(options, text="遮罩网格（0=自动）：").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(options, from_=0, to=64, textvariable=self.mask_grid_var, width=12).grid(
            row=1, column=1, sticky="ew", padx=(6, 18), pady=(8, 0))
        ttk.Label(options, text="随机种子（可选）：").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(options, textvariable=self.seed_var, width=14).grid(
            row=1, column=3, sticky="ew", padx=(6, 0), pady=(8, 0))

        ttk.Label(options, text="遮罩混合模式：").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(options, values=BLEND_MODES, state="readonly",
                     textvariable=self.mode_var, width=12).grid(
            row=2, column=1, sticky="ew", padx=(6, 18), pady=(8, 0))
        ttk.Label(options, text="颜色 1/2 混合：").grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Combobox(options, values=BLEND_MODES, state="readonly",
                     textvariable=self.blend12_var, width=12).grid(
            row=2, column=3, sticky="ew", padx=(6, 0), pady=(8, 0))

        checks = ttk.Frame(options)
        checks.grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Checkbutton(checks, text="逐帧随机采样颜色贴图",
                        variable=self.variation_var).pack(side="left")
        ttk.Checkbutton(checks, text="同时生成横向预览图",
                        variable=self.preview_var).pack(side="left", padx=(18, 0))

        actions = ttk.Frame(outer)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 8))
        self.run_button = ttk.Button(actions, text="开始合成", command=self.start)
        self.run_button.pack(side="left")
        self.open_button = ttk.Button(actions, text="打开输出目录", command=self.open_output_folder,
                                      state="disabled")
        self.open_button.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=180)
        self.progress.pack(side="right", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

        log_box = ttk.LabelFrame(outer, text="运行日志", padding=6)
        log_box.grid(row=3, column=0, sticky="nsew")
        outer.rowconfigure(3, weight=1)
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self.log = tk.Text(log_box, wrap="word", state="disabled", height=16)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_box, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        hint = ttk.Label(outer, foreground="#666",
                         text="提示：较大的网格和单格尺寸会显著增加处理时间与内存占用。")
        hint.grid(row=4, column=0, sticky="w", pady=(8, 0))

    def add_file_row(self, parent, row, label, variable, command):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(parent, text="浏览…", command=command, width=9).grid(
            row=row, column=2, padx=(6, 0), pady=4)

    def pick_input(self, variable, title):
        filename = filedialog.askopenfilename(title=title, filetypes=FILE_TYPES)
        if filename:
            variable.set(filename)

    def pick_mask(self):
        filename = filedialog.askopenfilename(title="选择遮罩贴图", filetypes=FILE_TYPES)
        if filename:
            self.mask_var.set(filename)
            if not self.output_var.get().strip():
                path = Path(filename)
                self.output_var.set(str(path.with_name(path.stem + "_combined.png")))

    def pick_output(self):
        filename = filedialog.asksaveasfilename(
            title="选择输出 PNG", defaultextension=".png",
            filetypes=[("PNG 文件", "*.png")])
        if filename:
            self.output_var.set(filename)

    def validate(self):
        mask = Path(self.mask_var.get().strip())
        color1 = Path(self.color1_var.get().strip())
        color2_text = self.color2_var.get().strip()
        color2 = Path(color2_text) if color2_text else None
        output_text = self.output_var.get().strip()

        for label, path in (("遮罩贴图", mask), ("颜色贴图 1", color1)):
            if not str(path) or not path.is_file():
                raise ValueError(f"请选择有效的{label}文件。")
        if color2 is not None and not color2.is_file():
            raise ValueError("颜色贴图 2 的文件不存在。")
        if not output_text:
            raise ValueError("请选择输出 PNG 的保存位置。")

        output = Path(output_text)
        if output.suffix.lower() != ".png":
            output = output.with_suffix(".png")
            self.output_var.set(str(output))

        try:
            grid = int(self.grid_var.get())
            cell = int(self.cell_var.get())
            mask_grid = int(self.mask_grid_var.get())
        except ValueError as exc:
            raise ValueError("网格和单格尺寸必须是整数。") from exc
        if grid < 1 or cell < 1 or mask_grid < 0:
            raise ValueError("输出网格和单格尺寸必须大于 0，遮罩网格不能小于 0。")
        if grid * cell > 16384:
            raise ValueError("输出图片边长不能超过 16384 像素。")

        seed_text = self.seed_var.get().strip()
        try:
            seed = int(seed_text) if seed_text else None
        except ValueError as exc:
            raise ValueError("随机种子必须是整数，或留空。") from exc

        return {
            "mask": mask,
            "color1": color1,
            "color2": color2,
            "output": output,
            "grid": grid,
            "cell": cell,
            "mask_grid": mask_grid,
            "mode": self.mode_var.get(),
            "blend12": self.blend12_var.get(),
            "variation": self.variation_var.get(),
            "preview": self.preview_var.get(),
            "seed": seed,
        }

    def start(self):
        if self.worker is not None and self.worker.is_alive():
            return
        try:
            config = self.validate()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self.root)
            return

        output_size = config["grid"] * config["cell"]
        if output_size > 2048:
            proceed = messagebox.askyesno(
                "输出尺寸较大",
                f"输出图片为 {output_size}×{output_size} 像素，可能耗时较长。\n是否继续？",
                parent=self.root)
            if not proceed:
                return

        self.clear_log()
        self.last_output = None
        self.open_button.configure(state="disabled")
        self.run_button.configure(state="disabled")
        self.status_var.set("正在处理…")
        self.progress.start(12)
        self.worker = threading.Thread(target=self.process, args=(config,), daemon=True)
        self.worker.start()
        self.root.after(80, self.poll_events)

    def process(self, config):
        writer = QueueWriter(self.events)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                config["output"].parent.mkdir(parents=True, exist_ok=True)
                if config["seed"] is not None:
                    random.seed(config["seed"])
                print("=" * 48)
                print("Multi Particle Texture Combiner")
                print("=" * 48)
                atlas = create_composite_atlas(
                    str(config["mask"]), str(config["color1"]), str(config["output"]),
                    color2_path=str(config["color2"]) if config["color2"] else None,
                    grid_size=config["grid"], cell_size=config["cell"],
                    mode=config["mode"], blend12=config["blend12"],
                    add_variation=config["variation"],
                    mask_grid_override=config["mask_grid"] or None,
                )
                if config["preview"]:
                    preview_path = config["output"].with_name(
                        config["output"].stem + "_preview.png")
                    self.save_preview(atlas, preview_path, config["grid"])
                    print(f"Preview saved: {preview_path}")
                print("\n处理完成。")
            self.events.put(("success", str(config["output"])))
        except Exception:
            self.events.put(("error", traceback.format_exc()))

    @staticmethod
    def save_preview(atlas, output_path, grid):
        """输出第一行预览，支持任意输出网格。"""
        cell_size = atlas.size[0] // grid
        count = min(grid, 8)
        preview = Image.new("RGBA", (cell_size * count, cell_size), (32, 32, 32, 255))
        for i in range(count):
            cell = atlas.crop((i * cell_size, 0, (i + 1) * cell_size, cell_size))
            preview.paste(cell, (i * cell_size, 0), cell)
        preview.save(output_path)

    def poll_events(self):
        finished = False
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "success":
                    self.append_log(payload + "\n")
                    self.last_output = Path(payload)
                    self.status_var.set("处理完成")
                    self.open_button.configure(state="normal")
                    messagebox.showinfo("完成", f"贴图已保存到：\n{payload}", parent=self.root)
                    finished = True
                elif kind == "error":
                    self.append_log(payload + "\n")
                    self.status_var.set("处理失败")
                    messagebox.showerror("处理失败", "合成过程中发生错误，详情见运行日志。",
                                         parent=self.root)
                    finished = True
        except queue.Empty:
            pass

        if finished:
            self.progress.stop()
            self.run_button.configure(state="normal")
        elif self.worker is not None and self.worker.is_alive():
            self.root.after(80, self.poll_events)
        else:
            self.progress.stop()
            self.run_button.configure(state="normal")
            if self.status_var.get() == "正在处理…":
                self.status_var.set("已停止")

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def open_output_folder(self):
        if not self.last_output:
            return
        folder = self.last_output.parent
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def on_close(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showwarning("正在处理", "请等待当前贴图合成完成后再退出。", parent=self.root)
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    TextureCombinerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

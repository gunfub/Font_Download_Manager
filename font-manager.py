# font_manager.py
# Windows 专用字体管理器（Tkinter GUI）
# 依赖: python 标准库 + 已有的 github_auth.GitHubManager（使用 keyring 中的 token）
# Put this file alongside your github_auth.py

import os
import json
import shutil
import ctypes
import threading
from pathlib import Path
from tkinter import Tk, Toplevel, Frame, Label, Entry, Button, Listbox, Scrollbar, END, SINGLE, messagebox, StringVar, ttk
import tkinter as tk
import winreg

from github_auth import GitHubManager  # uses your existing auth & token storage

# —— 常量与路径 —— #
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPOS_DIR = DATA_DIR / "repos"
REPOS_CONFIG = DATA_DIR / "repos.json"
INDEX_FILE = DATA_DIR / "index.json"
INSTALLED_FILE = DATA_DIR / "installed.json"

LOCAL_FONTS_DIR = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"

def download_from_raw(owner, repo, path, save_to, branch="main", token=None):
    import requests
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"下载失败 {r.status_code}: {url}")
    with open(save_to, "wb") as f:
        f.write(r.content)
    return save_to

# Windows API helpers
gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32
FR_PRIVATE = 0x10
WM_FONTCHANGE = 0x001D
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_FONTS_DIR.mkdir(parents=True, exist_ok=True)
ensure_dirs()

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return default
    return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

# —— 仓库配置管理 —— #
class RepoConfig:
    def __init__(self):
        self.repos = load_json(REPOS_CONFIG, [])

    def add_repo(self, owner, repo, descriptor="fonts.json"):
        key = f"{owner}/{repo}"
        if any(r["key"] == key for r in self.repos):
            raise ValueError("仓库已存在")
        entry = {
            "key": key,
            "owner": owner,
            "repo": repo,
            "descriptor": descriptor,
            "enabled": True
        }
        self.repos.append(entry)
        self.save()
        return entry

    def remove_repo(self, key):
        self.repos = [r for r in self.repos if r["key"] != key]
        self.save()

    def save(self):
        save_json(REPOS_CONFIG, self.repos)

# —— 索引合并与同步 —— #
class Indexer:
    def __init__(self, github_manager: GitHubManager):
        self.github = github_manager
        self.repo_cfg = RepoConfig()
        self.index = load_json(INDEX_FILE, {})  # key -> {meta, sources: [{repo,key,file}], merged_keys...}
        self.lock = threading.Lock()

    def refresh_all(self):
        """从每个已添加仓库拉取 descriptor 并更新合并索引"""
        new_index = {}
        for r in list(self.repo_cfg.repos):
            if not r.get("enabled", True):
                continue
            owner, repo = r["owner"], r["repo"]
            descriptor_path = r.get("descriptor", "fonts.json")
            try:
                repo_local = REPOS_DIR / f"{owner}_{repo}"
                repo_local.mkdir(parents=True, exist_ok=True)
                tmp_desc = repo_local / "_descriptor.json"
                token = self.github.github_auth.get_stored_token() if hasattr(self.github, "github_auth") else None
                download_from_raw(owner, repo, descriptor_path, tmp_desc, branch="main", token=token)
                desc = json.loads(tmp_desc.read_text(encoding='utf-8'))
                fonts = desc.get("fonts", [])
                for f in fonts:
                    fid = f.get("id") or f"{f.get('family','')}_{f.get('name','')}".replace(" ", "_")
                    meta = {
                        "id": fid,
                        "name": f.get("name"),
                        "family": f.get("family"),
                        "style": f.get("style"),
                        "version": f.get("version"),
                        "license": f.get("license"),
                        "files": f.get("files", [])
                    }
                    entry = new_index.setdefault(fid, {"meta": meta, "sources": []})
                    entry["sources"].append({
                        "repo_key": r["key"],
                        "owner": owner,
                        "repo": repo,
                        "descriptor": descriptor_path,
                        "files": f.get("files", [])
                    })
            except Exception as e:
                print(f"[warn] fetch descriptor failed for {r['key']}: {e}")
                continue
        with self.lock:
            self.index = new_index
            save_json(INDEX_FILE, self.index)
        return self.index

    def get_index(self):
        with self.lock:
            return self.index

# —— 安装 / 卸载 字体 —— #
class WindowsFontInstaller:
    @staticmethod
    def _broadcast_font_change():
        user32.SendMessageTimeoutW(HWND_BROADCAST, WM_FONTCHANGE, 0, 0, SMTO_ABORTIFHUNG, 1000, ctypes.byref(ctypes.c_ulong()))

    @staticmethod
    def _add_font_resource(font_path: str):
        res = gdi32.AddFontResourceExW(str(font_path), FR_PRIVATE, 0)
        return res

    @staticmethod
    def install_font_file(src_path: Path, display_name: str):
        if not src_path.exists():
            raise FileNotFoundError(src_path)
        dest = LOCAL_FONTS_DIR / src_path.name
        shutil.copy2(src_path, dest)
        key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.SetValueEx(key, display_name, 0, winreg.REG_SZ, dest.name)
            except Exception as e:
                print(f"[warn] registry set failed: {e}")
        try:
            WindowsFontInstaller._add_font_resource(str(dest))
        except Exception as e:
            print(f"[warn] AddFontResourceExW failed: {e}")
        WindowsFontInstaller._broadcast_font_change()
        return dest

    @staticmethod
    def uninstall_font(display_name: str, filename: str):
        key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, display_name)
                except FileNotFoundError:
                    pass
        except Exception as e:
            print(f"[warn] registry open failed: {e}")
        local_file = LOCAL_FONTS_DIR / filename
        try:
            if local_file.exists():
                local_file.unlink()
        except Exception as e:
            print(f"[warn] remove font file failed: {e}")
        WindowsFontInstaller._broadcast_font_change()

# —— GUI —— #
class FontManagerGUI:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Font Manager")
        self.github = GitHubManager()
        self.indexer = Indexer(self.github)
        self.repo_cfg = self.indexer.repo_cfg
        self.installed = load_json(INSTALLED_FILE, {})
        self.setup_ui()
        self.refresh_index_threaded()

    def setup_ui(self):
        self.root.geometry("1000x640")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Notebook(self.root)
        main.pack(fill="both", expand=True)

        tab_repos = Frame(main)
        main.add(tab_repos, text="仓库管理")
        self.setup_repos_tab(tab_repos)

        tab_fonts = Frame(main)
        main.add(tab_fonts, text="字体总表")
        self.setup_fonts_tab(tab_fonts)

        tab_installed = Frame(main)
        main.add(tab_installed, text="已安装")
        self.setup_installed_tab(tab_installed)

    # --- Repos tab --- #
    def setup_repos_tab(self, parent):
        frame = parent
        Label(frame, text="已添加仓库：").pack(anchor="nw")
        self.repo_listbox = Listbox(frame, width=60, height=10)
        self.repo_listbox.pack(side="left", fill="y", padx=8, pady=8)
        scrollbar = Scrollbar(frame, orient="vertical", command=self.repo_listbox.yview)
        scrollbar.pack(side="left", fill="y")
        self.repo_listbox.config(yscrollcommand=scrollbar.set)

        right = Frame(frame)
        right.pack(side="left", fill="both", expand=True, padx=8)

        Label(right, text="添加新仓库（owner/repo）：").pack(anchor="nw")
        self.owner_var = StringVar()
        self.repo_var = StringVar()
        Entry(right, textvariable=self.owner_var).pack(fill="x")
        Entry(right, textvariable=self.repo_var).pack(fill="x", pady=(6,0))
        Label(right, text="描述文件路径（仓库中，例如 fonts.json）：").pack(anchor="nw", pady=(8,0))
        self.desc_var = StringVar(value="fonts.json")
        Entry(right, textvariable=self.desc_var).pack(fill="x")
        Button(right, text="添加仓库", command=self.on_add_repo).pack(pady=(8,4))
        Button(right, text="删除选中仓库", command=self.on_remove_repo).pack(pady=4)
        Button(right, text="刷新并合并索引", command=self.refresh_index_threaded).pack(pady=4)
        self.load_repo_listbox()

    def load_repo_listbox(self):
        self.repo_listbox.delete(0, END)
        for r in self.repo_cfg.repos:
            self.repo_listbox.insert(END, f"{r['key']}  [{r.get('descriptor','fonts.json')}]")

    def on_add_repo(self):
        owner = self.owner_var.get().strip()
        repo = self.repo_var.get().strip()
        desc = self.desc_var.get().strip() or "fonts.json"
        if not owner or not repo:
            messagebox.showerror("错误", "请填写 owner 和 repo")
            return
        try:
            self.repo_cfg.add_repo(owner, repo, desc)
            self.load_repo_listbox()
            messagebox.showinfo("成功", "仓库已添加（请点击刷新以拉取 descriptor）")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def on_remove_repo(self):
        sel = self.repo_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        key = self.repo_cfg.repos[idx]["key"]
        if messagebox.askyesno("确认", f"确定删除仓库 {key} 吗？"):
            self.repo_cfg.remove_repo(key)
            self.load_repo_listbox()

    # --- Fonts tab --- #
    def setup_fonts_tab(self, parent):
        frame = parent
        top = Frame(frame)
        top.pack(fill="x")
        Label(top, text="搜索:").pack(side="left")
        self.search_var = StringVar()
        Entry(top, textvariable=self.search_var).pack(side="left", fill="x", expand=True, padx=4)
        Button(top, text="搜索", command=self.refresh_fonts_view).pack(side="left", padx=4)
        Button(top, text="刷新索引", command=self.refresh_index_threaded).pack(side="left", padx=4)

        middle = Frame(frame)
        middle.pack(fill="both", expand=True, padx=6, pady=6)
        self.fonts_tree = ttk.Treeview(middle, columns=("family","style","sources"), show="headings")
        self.fonts_tree.heading("family", text="Family")
        self.fonts_tree.heading("style", text="Style")
        self.fonts_tree.heading("sources", text="来源仓库数量")
        self.fonts_tree.pack(fill="both", expand=True, side="left")
        scrollbar = Scrollbar(middle, orient="vertical", command=self.fonts_tree.yview)
        scrollbar.pack(side="left", fill="y")
        self.fonts_tree.config(yscrollcommand=scrollbar.set)

        bottom = Frame(frame)
        bottom.pack(fill="x")
        Button(bottom, text="查看来源并安装选中字体", command=self.on_install_selected).pack(side="left", padx=6)
        Button(bottom, text="刷新列表", command=self.refresh_fonts_view).pack(side="left", padx=6)

    def refresh_fonts_view(self):
        idx = self.indexer.get_index()
        q = self.search_var.get().lower().strip()
        for i in self.fonts_tree.get_children():
            self.fonts_tree.delete(i)
        for fid, info in idx.items():
            name = info["meta"].get("name") or fid
            family = info["meta"].get("family") or ""
            style = info["meta"].get("style") or ""
            sources = len(info.get("sources", []))
            if q and q not in name.lower() and q not in family.lower():
                continue
            self.fonts_tree.insert("", END, iid=fid, values=(family, style, sources))

    # --- 安装字体（多文件 + 下载进度窗口） --- #
    def on_install_selected(self):
        sel = self.fonts_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择字体")
            return
        fid = sel[0]
        info = self.indexer.get_index().get(fid)
        if not info:
            messagebox.showerror("错误", "索引中未找到该字体")
            return
        sources = info.get("sources", [])
        if len(sources) == 1:
            chosen = sources[0]
        else:
            chosen = self.ask_source_choice(sources)
            if not chosen:
                return
        t = threading.Thread(target=self._install_font_files_thread, args=(info, chosen), daemon=True)
        t.start()

    def _install_font_files_thread(self, info, chosen):
        owner = chosen["owner"]
        repo = chosen["repo"]
        repo_key = chosen["repo_key"]
        repo_local = REPOS_DIR / f"{owner}_{repo}"
        repo_local.mkdir(parents=True, exist_ok=True)
        files = chosen.get("files", [])
        if not files:
            self._show_error("错误", "该来源未列出文件")
            return

        # 弹出进度窗口（大一些，居中）
        progress_win = Toplevel(self.root)
        progress_win.title(f"安装字体 {info['meta'].get('name')}")
        progress_win.geometry("400x120")
        progress_win.resizable(False, False)
        
        # 居中屏幕
        progress_win.update_idletasks()
        w = progress_win.winfo_width()
        h = progress_win.winfo_height()
        ws = progress_win.winfo_screenwidth()
        hs = progress_win.winfo_screenheight()
        x = (ws // 2) - (w // 2)
        y = (hs // 2) - (h // 2)
        progress_win.geometry(f"{w}x{h}+{x}+{y}")

        Label(progress_win, text=f"正在安装字体 {info['meta'].get('name')}").pack(pady=6)
        progress_var = StringVar()
        lbl = Label(progress_win, textvariable=progress_var)
        lbl.pack(pady=4)

        pb = ttk.Progressbar(progress_win, length=350, mode="determinate", maximum=len(files))
        pb.pack(pady=6)

        progress_win.transient(self.root)
        progress_win.grab_set()
        progress_win.update()

        for i, file_rel in enumerate(files, start=1):
            progress_var.set(f"正在下载第 {i} / {len(files)} 个文件: {file_rel}")
            pb['value'] = i - 1
            progress_win.update_idletasks()

            target_save = repo_local / Path(file_rel).name
            try:
                self.github.download_file(owner, repo, file_rel, str(target_save))
                meta = info["meta"]
                display_name = f"{meta.get('name') or info['meta']['id']} ({meta.get('style') or i})".strip()
                WindowsFontInstaller.install_font_file(target_save, display_name)
                self.installed[display_name] = {
                    "filename": target_save.name,
                    "source": repo_key,
                    "id": info['meta']['id']
                }
            except Exception as e:
                self._show_error("下载失败", str(e))

        pb['value'] = len(files)
        progress_var.set("安装完成！")
        save_json(INSTALLED_FILE, self.installed)
        self.root.after(0, self.load_installed_list)
        progress_win.after(500, progress_win.destroy)


    def _show_error(self, title, msg):
        self.root.after(0, lambda: messagebox.showerror(title, msg))

    def _show_info(self, title, msg):
        self.root.after(0, lambda: messagebox.showinfo(title, msg))

    def ask_source_choice(self, sources):
        win = Toplevel(self.root)
        win.title("选择来源仓库")
        win.geometry("520x300")
        Label(win, text="检测到多个来源，请选择要安装的来源：").pack()
        lb = Listbox(win, selectmode=SINGLE)
        lb.pack(fill="both", expand=True, padx=6, pady=6)
        for s in sources:
            lb.insert(END, f"{s['repo_key']}  -> files: {', '.join(s.get('files',[]))}")
        chosen = {"value": None}
        def on_ok():
            cur = lb.curselection()
            if not cur:
                messagebox.showwarning("提示", "请选择一个来源")
                return
            chosen["value"] = sources[cur[0]]
            win.destroy()
        Button(win, text="确定", command=on_ok).pack(pady=6)
        win.transient(self.root)
        win.grab_set()
        self.root.wait_window(win)
        return chosen["value"]

    # --- Installed tab --- #
    def setup_installed_tab(self, parent):
        frame = parent
        Label(frame, text="已安装字体：").pack(anchor="nw")
        self.installed_listbox = Listbox(frame, width=80, height=18)
        self.installed_listbox.pack(fill="both", expand=True, padx=6, pady=6)
        btn_frame = Frame(frame)
        btn_frame.pack(fill="x")
        Button(btn_frame, text="卸载选中字体", command=self.on_uninstall_selected).pack(side="left", padx=6)
        Button(btn_frame, text="打开字体目录", command=lambda: os.startfile(LOCAL_FONTS_DIR)).pack(side="left", padx=6)
        self.load_installed_list()

    def load_installed_list(self):
        self.installed_listbox.delete(0, END)
        for name, info in self.installed.items():
            self.installed_listbox.insert(END, f"{name}  <- file: {info.get('filename')}  from: {info.get('source')}")

    def on_uninstall_selected(self):
        sel = self.installed_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        name = list(self.installed.keys())[idx]
        info = self.installed[name]
        if not messagebox.askyesno("确认", f"确定要卸载字体 {name} 吗？"):
            return
        try:
            WindowsFontInstaller.uninstall_font(name, info.get("filename"))
            del self.installed[name]
            save_json(INSTALLED_FILE, self.installed)
            messagebox.showinfo("已卸载", f"{name} 已卸载")
            self.load_installed_list()
        except Exception as e:
            messagebox.showerror("卸载失败", str(e))

    # --- 刷新索引线程化 --- #
    def refresh_index_threaded(self):
        def job():
            try:
                self.indexer.refresh_all()
                self.refresh_fonts_view()
                self._show_info("完成", "索引刷新并合并完成")
            except Exception as e:
                self._show_error("错误", f"刷新索引失败: {e}")
        t = threading.Thread(target=job, daemon=True)
        t.start()

# —— 程序入口 —— #
def run_gui():
    root = Tk()
    app = FontManagerGUI(root)
    root.mainloop()

if __name__ == "__main__":
    run_gui()

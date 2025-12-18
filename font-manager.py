# font_manager.py
# Windows 专用字体管理器（Tkinter GUI）
# 依赖: python 标准库 + 已有的 github_auth.GitHubManager（使用 keyring 中的 token）
# Put this file alongside your github_auth.py

import os
import json
import shutil
import hashlib
import ctypes
import threading
import requests
from pathlib import Path
from tkinter import Tk, Toplevel, Frame, Label, Entry, Button, Listbox, Scrollbar, END, SINGLE, messagebox, StringVar, ttk
import tkinter as tk
import winreg

# 引入你已有的 GitHub 管理类（假设 github_auth.py 在同目录）
from github_auth import GitHubManager  # uses your existing auth & token storage

# —— 常量与路径 —— #
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPOS_DIR = DATA_DIR / "repos"
REPOS_CONFIG = DATA_DIR / "repos.json"
INDEX_FILE = DATA_DIR / "index.json"
INSTALLED_FILE = DATA_DIR / "installed.json"

# Windows per-user fonts folder
LOCAL_FONTS_DIR = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"


def download_from_raw(owner, repo, path, save_to, branch="main", token=None):
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
    else:
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
                # 下载 descriptor
                tmp_desc = repo_local / "_descriptor.json"
                # 通过 GitHubAuth 的 get_stored_token 方法获取令牌
                token = self.github.github_auth.get_stored_token() if hasattr(self.github, "github_auth") else None
                download_from_raw(owner, repo, descriptor_path, tmp_desc, branch="main", token=token)
                desc = json.loads(tmp_desc.read_text(encoding='utf-8'))
                fonts = desc.get("fonts", [])
                for f in fonts:
                    # canonical id
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
                # 拉取某个仓库 descriptor 失败：记录日志但继续
                print(f"[warn] fetch descriptor failed for {r['key']}: {e}")
                continue
        # 保存
        with self.lock:
            self.index = new_index
            save_json(INDEX_FILE, self.index)
        return self.index

    def get_index(self):
        with self.lock:
            return self.index

# —— 安装 / 卸载 字体 —— #
class WindowsFontInstaller:
    def __init__(self):
        pass

    @staticmethod
    def _broadcast_font_change():
        # SendMessageTimeoutW(HWND_BROADCAST, WM_FONTCHANGE, 0, 0, SMTO_ABORTIFHUNG, 1000, None)
        user32.SendMessageTimeoutW(HWND_BROADCAST, WM_FONTCHANGE, 0, 0, SMTO_ABORTIFHUNG, 1000, ctypes.byref(ctypes.c_ulong()))

    @staticmethod
    def _add_font_resource(font_path: str):
        # AddFontResourceExW expects wide string
        res = gdi32.AddFontResourceExW(str(font_path), FR_PRIVATE, 0)
        return res

    @staticmethod
    def install_font_file(src_path: Path, display_name: str):
        """
        将字体文件复制到 per-user fonts dir，并在 HKCU 注册表注册，然后广播字体变更。
        display_name: Windows 下显示的字体名称（例如 "Inter Regular (TrueType)"）
        """
        if not src_path.exists():
            raise FileNotFoundError(src_path)

        dest = LOCAL_FONTS_DIR / src_path.name
        shutil.copy2(src_path, dest)

        # 写入 HKCU 注册表
        key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            # Value name 通常是 "Font Name (TrueType)" 或类似。我们用 display_name
            try:
                winreg.SetValueEx(key, display_name, 0, winreg.REG_SZ, dest.name)
            except Exception as e:
                print(f"[warn] registry set failed: {e}")

        # 尝试动态加载到当前会话（有时不必要）
        try:
            WindowsFontInstaller._add_font_resource(str(dest))
        except Exception as e:
            print(f"[warn] AddFontResourceExW failed: {e}")

        # 广播
        WindowsFontInstaller._broadcast_font_change()
        return dest

    @staticmethod
    def uninstall_font(display_name: str, filename: str):
        # 删除注册表项并删除文件（file 在 LOCAL_FONTS_DIR）
        key_path = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, display_name)
                except FileNotFoundError:
                    pass
        except Exception as e:
            print(f"[warn] registry open failed: {e}")
        # 删除文件
        local_file = LOCAL_FONTS_DIR / filename
        try:
            if local_file.exists():
                local_file.unlink()
        except Exception as e:
            print(f"[warn] remove font file failed: {e}")
        # broadcast
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
        # initial load
        self.refresh_index_threaded()

    def setup_ui(self):
        self.root.geometry("1000x640")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Notebook(self.root)
        main.pack(fill="both", expand=True)

        # Repos tab
        tab_repos = Frame(main)
        main.add(tab_repos, text="仓库管理")
        self.setup_repos_tab(tab_repos)

        # Fonts tab
        tab_fonts = Frame(main)
        main.add(tab_fonts, text="字体总表")
        self.setup_fonts_tab(tab_fonts)

        # Installed tab
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
        # 如果 sources >1，让用户选择来源
        sources = info.get("sources", [])
        if len(sources) == 1:
            chosen = sources[0]
        else:
            chosen = self.ask_source_choice(sources)
            if not chosen:
                return

        # 将 chosen.files 中第一个有效文件下载到本地仓库文件夹，然后安装
        owner = chosen["owner"]
        repo = chosen["repo"]
        repo_key = chosen["repo_key"]
        repo_local = REPOS_DIR / f"{owner}_{repo}"
        repo_local.mkdir(parents=True, exist_ok=True)
        files = chosen.get("files", [])
        if not files:
            messagebox.showerror("错误", "该来源未列出文件")
            return
        target_file_rel = files[0]
        target_save = repo_local / Path(target_file_rel).name
        try:
            # 下载字体文件（会使用已保存的 token）
            self.github.download_file(owner, repo, target_file_rel, str(target_save))
        except Exception as e:
            messagebox.showerror("下载失败", str(e))
            return

        # 计算 display_name（尽量使用 meta.name + style）
        meta = info["meta"]
        display_name = f"{meta.get('name') or fid} ({meta.get('style') or ''})".strip()
        try:
            dest = WindowsFontInstaller.install_font_file(target_save, display_name)
            # 记录已安装
            self.installed[display_name] = {
                "filename": dest.name,
                "source": repo_key,
                "id": fid
            }
            save_json(INSTALLED_FILE, self.installed)
            messagebox.showinfo("安装成功", f"字体已安装到 {dest}")
            self.load_installed_list()
        except Exception as e:
            messagebox.showerror("安装失败", str(e))

    def ask_source_choice(self, sources):
        # 弹窗让用户选择来源（简单列表选择）
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
            idx = cur[0]
            chosen["value"] = sources[idx]
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
            # remove record
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
                messagebox.showinfo("完成", "索引刷新并合并完成")
            except Exception as e:
                messagebox.showerror("错误", f"刷新索引失败: {e}")
        t = threading.Thread(target=job, daemon=True)
        t.start()

# —— 程序入口 —— #
def run_gui():
    root = Tk()
    app = FontManagerGUI(root)
    root.mainloop()

if __name__ == "__main__":
    run_gui()

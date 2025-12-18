# font_manager.py
# Windows 专用字体管理器（Tkinter GUI）
# 依赖: python 标准库 + github_auth.GitHubManager

import os
import json
import shutil
import threading
from pathlib import Path
from tkinter import Tk, Toplevel, Frame, Label, Entry, Button, Listbox, Scrollbar, END, messagebox, StringVar, ttk
from github_auth import GitHubManager

# —— 常量与路径 —— #
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPOS_DIR = DATA_DIR / "repos"
REPOS_CONFIG = DATA_DIR / "repos.json"
INDEX_FILE = DATA_DIR / "index.json"
INSTALLED_FILE = DATA_DIR / "installed.json"
TMP_DIR = DATA_DIR / "tmp"

WINDOWS_FONTS_DIR = Path("C:/Windows/Fonts")

# —— 工具函数 —— #
def ensure_dirs():
    for d in [DATA_DIR, REPOS_DIR, TMP_DIR]:
        d.mkdir(parents=True, exist_ok=True)
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

# —— 仓库管理 —— #
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
        self.index = load_json(INDEX_FILE, {})
        self.lock = threading.Lock()

    def refresh_all(self):
        new_index = {}
        for r in list(self.repo_cfg.repos):
            if not r.get("enabled", True):
                continue
            owner, repo = r["owner"], r["repo"]
            descriptor_path = r.get("descriptor", "fonts.json")
            try:
                tmp_desc = REPOS_DIR / f"{owner}_{repo}_descriptor.json"
                self.github.download_file(owner, repo, descriptor_path, str(tmp_desc))
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

# —— 临时下载 / 引导安装 —— #
class FontDownloader:
    @staticmethod
    def download_to_tmp(owner, repo, files, progress_callback=None):
        tmp_files = []
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        for i, f_rel in enumerate(files, start=1):
            filename = Path(f_rel).name
            save_path = TMP_DIR / filename
            try:
                GitHubManager().download_file(owner, repo, f_rel, str(save_path))
            except Exception as e:
                raise RuntimeError(f"下载 {f_rel} 失败: {e}")
            tmp_files.append(save_path)
            if progress_callback:
                progress_callback(i, len(files), filename)
        return tmp_files

    @staticmethod
    def open_folder(path: Path):
        if path.exists():
            os.startfile(path)

    @staticmethod
    def show_install_instructions(tmp_dir: Path):
        win = Toplevel()
        win.title("安装字体指引")
        win.geometry("480x150")
        ws, hs = win.winfo_screenwidth(), win.winfo_screenheight()
        x = (ws // 2) - 240
        y = (hs // 2) - 75
        win.geometry(f"+{x}+{y}")

        Label(win, text="字体已下载到临时文件夹。\n请将文件拖拽到 C:\\Windows\\Fonts 完成安装。").pack(expand=True, pady=10)

        btn_frame = Frame(win)
        btn_frame.pack(pady=10)
        Button(btn_frame, text="打开临时文件夹", command=lambda: FontDownloader.open_folder(tmp_dir)).pack(side="left", padx=6)
        Button(btn_frame, text="打开系统字体文件夹", command=lambda: FontDownloader.open_folder(WINDOWS_FONTS_DIR)).pack(side="left", padx=6)
        Button(btn_frame, text="清空临时文件夹", command=lambda: FontDownloader.clear_tmp_folder(tmp_dir)).pack(side="left", padx=6)

        win.transient()
        win.grab_set()
        win.wait_window()

    @staticmethod
    def show_uninstall_instructions(filenames, gui_ref=None):
        """
        filenames: list[str]，支持多选
        gui_ref: FontManagerGUI 实例，用于更新已安装列表
        """
        win = Toplevel()
        win.title("卸载字体指引")
        win.geometry("420x130")
        x, y = 50, 50
        win.geometry(f"+{x}+{y}")

        Label(win, text="请在系统字体文件夹中找到以下字体并右键删除完成卸载：").pack(pady=6)
        Label(win, text="\n".join(filenames)).pack(expand=True, pady=4)

        btn_frame = Frame(win)
        btn_frame.pack(pady=10)
        Button(btn_frame, text="打开系统字体文件夹", command=lambda: FontDownloader.open_folder(WINDOWS_FONTS_DIR)).pack(side="left", padx=6)

        def confirm_uninstall():
            if gui_ref:
                installed = gui_ref.installed
                keys_to_remove = [k for k, v in installed.items() if v['filename'] in filenames]
                for k in keys_to_remove:
                    del installed[k]
                save_json(INSTALLED_FILE, installed)
                gui_ref.load_installed_list()
            win.destroy()

        Button(btn_frame, text="我已卸载", command=confirm_uninstall).pack(side="left", padx=6)

        win.transient()
        win.grab_set()
        win.wait_window()

    @staticmethod
    def clear_tmp_folder(tmp_dir: Path):
        if tmp_dir.exists():
            for f in tmp_dir.iterdir():
                try:
                    if f.is_file():
                        f.unlink()
                    elif f.is_dir():
                        shutil.rmtree(f)
                except Exception as e:
                    print(f"[warn] 清理临时文件失败: {e}")
            messagebox.showinfo("提示", "临时文件夹已清空")

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
        # self.refresh_index_threaded()  # 注释掉启动时自动刷新索引

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
        main.add(tab_installed, text="已下载字体")
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
        # 设置多选
        self.fonts_tree = ttk.Treeview(middle, columns=("family","style","sources"), show="headings", selectmode="extended")
        self.fonts_tree.heading("family", text="Family")
        self.fonts_tree.heading("style", text="Style")
        self.fonts_tree.heading("sources", text="来源仓库数量")
        self.fonts_tree.pack(fill="both", expand=True, side="left")
        scrollbar = Scrollbar(middle, orient="vertical", command=self.fonts_tree.yview)
        scrollbar.pack(side="left", fill="y")
        self.fonts_tree.config(yscrollcommand=scrollbar.set)

        bottom = Frame(frame)
        bottom.pack(fill="x")
        Button(bottom, text="下载并安装选中字体", command=self.on_download_selected).pack(side="left", padx=6)
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

    def on_download_selected(self):
        sel = self.fonts_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择字体")
            return
        t = threading.Thread(target=self._download_fonts_thread, args=(sel,), daemon=True)
        t.start()

    def _download_fonts_thread(self, fids):
        for fid in fids:
            info = self.indexer.get_index().get(fid)
            if not info:
                continue
            sources = info.get("sources", [])
            chosen = sources[0] if len(sources)==1 else self.ask_source_choice(sources)
            if not chosen:
                continue
            owner = chosen["owner"]
            repo = chosen["repo"]
            files = chosen.get("files", [])
            if not files:
                continue

            def callback(i, total, filename):
                progress_var.set(f"正在下载 {i}/{total}: {filename}")
                pb['value'] = i
                progress_win.update_idletasks()

            progress_win = Toplevel(self.root)
            progress_win.title(f"下载字体 {info['meta'].get('name')}")
            progress_win.geometry("400x120")
            progress_win.transient(self.root)
            progress_win.grab_set()
            progress_var = StringVar()
            Label(progress_win, text=f"正在下载字体 {info['meta'].get('name')}").pack(pady=6)
            lbl = Label(progress_win, textvariable=progress_var)
            lbl.pack(pady=4)
            pb = ttk.Progressbar(progress_win, length=350, mode="determinate", maximum=len(files))
            pb.pack(pady=6)
            progress_win.update()

            try:
                tmp_files = FontDownloader.download_to_tmp(owner, repo, files, progress_callback=callback)
            except Exception as e:
                self._show_error("下载失败", str(e))
                progress_win.destroy()
                continue

            pb['value'] = len(files)
            progress_var.set("下载完成！")
            progress_win.after(500, progress_win.destroy)

            FontDownloader.show_install_instructions(TMP_DIR)

            for f in tmp_files:
                self.installed[f.name] = {
                    "filename": f.name,
                    "source": chosen["repo_key"],
                    "id": info['meta']['id']
                }
            save_json(INSTALLED_FILE, self.installed)
            self.root.after(0, self.load_installed_list)

    def _show_error(self, title, msg):
        self.root.after(0, lambda: messagebox.showerror(title, msg))

    def _show_info(self, title, msg):
        self.root.after(0, lambda: messagebox.showinfo(title, msg))

    def ask_source_choice(self, sources):
        win = Toplevel(self.root)
        win.title("选择来源仓库")
        win.geometry("520x300")
        Label(win, text="检测到多个来源，请选择要下载的来源：").pack()
        lb = Listbox(win, selectmode="single")
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
        Label(frame, text="已下载字体：").pack(anchor="nw")
        self.installed_listbox = Listbox(frame, width=80, height=18, selectmode="extended")
        self.installed_listbox.pack(fill="both", expand=True, padx=6, pady=6)
        btn_frame = Frame(frame)
        btn_frame.pack(fill="x")
        Button(btn_frame, text="引导卸载选中字体", command=self.on_uninstall_selected).pack(side="left", padx=6)
        Button(btn_frame, text="打开系统字体文件夹", command=lambda: FontDownloader.open_folder(WINDOWS_FONTS_DIR)).pack(side="left", padx=6)
        self.load_installed_list()

    def load_installed_list(self):
        self.installed_listbox.delete(0, END)
        for k, v in self.installed.items():
            self.installed_listbox.insert(END, f"{v['filename']}  [{v['source']}]")

    def on_uninstall_selected(self):
        sel = self.installed_listbox.curselection()
        if not sel:
            return
        keys = list(self.installed.keys())
        filenames = [self.installed[keys[i]]["filename"] for i in sel]
        FontDownloader.show_uninstall_instructions(filenames, gui_ref=self)

    # --- Refresh index in thread --- #
    def refresh_index_threaded(self):
        t = threading.Thread(target=self._refresh_index, daemon=True)
        t.start()

    def _refresh_index(self):
        self._show_info("索引刷新", "正在刷新字体索引，请稍候...")
        self.indexer.refresh_all()
        self.refresh_fonts_view()
        self._show_info("完成", "字体索引刷新完成")

# —— 程序入口 —— #
def run_gui():
    root = Tk()
    app = FontManagerGUI(root)
    root.mainloop()

if __name__ == "__main__":
    run_gui()

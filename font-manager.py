import keyring
import requests
import json
import secrets
import webview
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path  # 修正：正确导入 Path
import threading         # 修正：单独导入 threading
import webbrowser
from urllib.parse import parse_qs, urlparse
import pyperclip  # 用于复制到剪贴板
from http.server import HTTPServer, BaseHTTPRequestHandler  # 添加缺少的导入

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        
        # 提取code和state
        query_components = parse_qs(urlparse(self.path).query)
        
        success_html = '''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>授权结果</title>
            <style>
                body {
                    font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                    background-color: #f0f2f5;
                }
                .container {
                    text-align: center;
                    padding: 2rem;
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }
                h1 { color: #28a745; margin-bottom: 1rem; }
                .success { color: #28a745; }
                .error { color: #dc3545; }
                p { color: #666; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1 class="success">授权成功</h1>
                <p>已获取访问令牌，您可以关闭此窗口了</p>
            </div>
            <script>
                setTimeout(() => window.close(), 3000);
            </script>
        </body>
        </html>
        '''
        
        error_html = '''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>授权结果</title>
            <style>
                body {
                    font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    margin: 0;
                    background-color: #f0f2f5;
                }
                .container {
                    text-align: center;
                    padding: 2rem;
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }
                h1 { color: #dc3545; margin-bottom: 1rem; }
                .success { color: #28a745; }
                .error { color: #dc3545; }
                p { color: #666; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1 class="error">授权失败！</h1>
                <p>请重新尝试授权操作</p>
            </div>
        </body>
        </html>
        '''
        
        response_html = success_html if 'code' in query_components else error_html
        self.wfile.write(response_html.encode('utf-8'))
        
        if 'code' in query_components:
            self.server.oauth_code = query_components['code'][0]
            self.server.oauth_state = query_components.get('state', [None])[0]

class GitHubAuth:
    def __init__(self):
        self.SERVICE_NAME = "FontManagerGitHub"
        self.USERNAME = "default"
        self.config_path = Path(__file__).parent / "data" / "account.conf"
        self.ensure_config_file()
        self.load_config()
        
    def ensure_config_file(self):
        """确保配置文件存在且格式正确"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_config = {
            "github": {
                "client_id": "",
                "client_secret": "",
                "redirect_uri": "http://localhost:9826/callback",
                "scope": "repo",
                "auth_url": "https://github.com/login/oauth/authorize",
                "token_url": "https://github.com/login/oauth/access_token"
            }
        }
        
        needs_default = True
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                if (isinstance(config, dict) and 
                    'github' in config and 
                    isinstance(config['github'], dict) and
                    all(key in config['github'] for key in self.default_config['github'])):
                    needs_default = False
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
                
        if needs_default:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.default_config, f, indent=4)
            self.config = self.default_config
        else:
            self.config = config
    
    def load_config(self):
        """加载配置文件"""
        # ensure_config_file 已经设置了 self.config
        self.CLIENT_ID = self.config.get('github', {}).get('client_id', '')
        self.CLIENT_SECRET = self.config.get('github', {}).get('client_secret', '')
        self.REDIRECT_URI = self.config.get('github', {}).get('redirect_uri', 'http://localhost:9826/callback')
    
    def save_config(self):
        """保存配置文件"""
        self.config['github']['client_id'] = self.CLIENT_ID
        self.config['github']['client_secret'] = self.CLIENT_SECRET
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4)

    def get_stored_token(self):
        """从系统凭据管理器获取存储的令牌"""
        return keyring.get_password(self.SERVICE_NAME, self.USERNAME)
    
    def store_token(self, token):
        """将令牌安全存储到系统凭据管理器"""
        keyring.set_password(self.SERVICE_NAME, self.USERNAME, token)
    
    def verify_token(self, token):
        """验证令牌是否有效"""
        headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        response = requests.get('https://api.github.com/user', headers=headers)
        return response.status_code == 200
    
    def download_file(self, repo_owner, repo_name, file_path, save_path):
        """从GitHub仓库下载文件
        
        Args:
            repo_owner (str): 仓库所有者
            repo_name (str): 仓库名称
            file_path (str): 仓库中的文件路径
            save_path (str): 保存到本地的路径
        """
        token = self.get_stored_token()
        if not token:
            raise ValueError("未登录GitHub账户")
            
        headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3.raw'
        }
        
        url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}'
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                f.write(response.content)
            return True
        else:
            raise Exception(f"下载失败: {response.json().get('message', '未知错误')}")

class FontManager:
    def __init__(self):
        self.github_auth = GitHubAuth()
        
    def setup_github_app(self):
        """使用tkinter引导用户设置GitHub OAuth应用"""
        class SetupWindow(tk.Toplevel):
            def __init__(self):
                super().__init__()
                self.credentials = None
                self.setup_ui()
                
            def setup_ui(self):
                self.title("GitHub应用设置指南")
                self.geometry("600x680")
                self.attributes('-topmost', True)  # 窗口置顶
                self.resizable(False, False)  # 禁止调整窗口大小
                
                # 设置窗口图标（使用感叹号图标表示这是引导窗口）
                try:
                    self.iconbitmap('warning')  # 使用系统自带的警告图标
                except:
                    pass  # 如果设置失败就使用默认图标
                
                style = ttk.Style()
                style.configure('Title.TLabel', font=("Arial", 16, "bold"))
                style.configure('Step.TLabelframe', padding=10)
                style.configure('Info.TLabel', font=("Arial", 10))
                style.configure('Action.TButton', padding=5)
                
                # 创建主框架，支持滚动
                main_frame = ttk.Frame(self)
                main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
                
                # 标题
                title = ttk.Label(main_frame, text="GitHub OAuth应用设置指南", style='Title.TLabel')
                title.pack(pady=(0, 20))
                
                # 第1步
                step1_frame = ttk.LabelFrame(main_frame, text="第1步：访问GitHub OAuth应用设置页面")
                step1_frame.pack(fill=tk.X, pady=(0, 10))
                
                ttk.Label(step1_frame, text="1. 登录GitHub").pack(padx=10, pady=5)
                url_frame = ttk.Frame(step1_frame)
                url_frame.pack(fill=tk.X, padx=10, pady=5)
                
                url = "https://github.com/settings/applications/new"
                ttk.Label(url_frame, text="2. 点击下方按钮在新窗口中注册应用").pack(side=tk.LEFT)
                ttk.Button(url_frame, text="注册GitHub应用", 
                          command=lambda: self.open_in_webview(url)).pack(side=tk.LEFT, padx=5)
                
                # 第2步
                step2_frame = ttk.LabelFrame(main_frame, text="第2步：填写应用信息")
                step2_frame.pack(fill=tk.X, pady=(0, 10))
                
                ttk.Label(step2_frame, text="在打开的GitHub页面中填写以下信息：", font=("Arial", 10, "bold")).pack(padx=10, pady=(5,0))
                
                info_frame = ttk.Frame(step2_frame)
                info_frame.pack(fill=tk.X, padx=10, pady=5)
                
                # 使用表格样式布局
                info_text = """
1. Application name: Font Manager（或其他名称）

2. Homepage URL:
   http://localhost:9826

3. Authorization callback URL:
   http://localhost:9826/callback

4. 应用描述（可选）：
   字体管理器 OAuth 应用
                """
                text_widget = tk.Text(info_frame, height=12, width=50)
                text_widget.insert('1.0', info_text)
                text_widget.configure(state='disabled', bg=self.cget('bg'))
                text_widget.pack(fill=tk.X, pady=5)
                
                url_copy_frame = ttk.Frame(info_frame)
                url_copy_frame.pack(fill=tk.X, pady=5)
                ttk.Button(url_copy_frame, text="复制 Homepage URL", 
                          command=lambda: self.copy_to_clipboard("http://localhost:9826")).pack(side=tk.LEFT, padx=5)
                ttk.Button(url_copy_frame, text="复制 Callback URL", 
                          command=lambda: self.copy_to_clipboard("http://localhost:9826/callback")).pack(side=tk.LEFT)
                
                ttk.Label(step2_frame, text="完成注册后，您将获得 Client ID 和 Client Secret", 
                         font=("Arial", 10, "italic")).pack(padx=10, pady=(0,5))
                
                # 第3步
                step3_frame = ttk.LabelFrame(main_frame, text="第3步：输入应用凭据")
                step3_frame.pack(fill=tk.X, pady=(0, 10))
                
                # Client ID输入框
                id_frame = ttk.Frame(step3_frame)
                id_frame.pack(fill=tk.X, padx=10, pady=5)
                ttk.Label(id_frame, text="Client ID:").pack(side=tk.LEFT)
                self.client_id = ttk.Entry(id_frame)
                self.client_id.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
                
                # Client Secret输入框
                secret_frame = ttk.Frame(step3_frame)
                secret_frame.pack(fill=tk.X, padx=10, pady=5)
                ttk.Label(secret_frame, text="Client Secret:").pack(side=tk.LEFT)
                self.client_secret = ttk.Entry(secret_frame)
                self.client_secret.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
                
                # 保存按钮
                ttk.Button(main_frame, text="保存凭据", command=self.save_credentials).pack(pady=20)
                
            def start_webview(self, url):
                """在主线程中启动webview，使用事件循环保持响应"""
                def on_shown():
                    # 确保引导窗口在前台显示
                    self.lift()
                    self.focus_force()
                
                def on_closed():
                    # webview窗口关闭时的回调
                    self.lift()
                    self.focus_force()

                # 创建webview窗口并设置位置
                window = webview.create_window(
                    'GitHub应用注册', 
                    url,
                    width=self.webview_width,
                    height=self.webview_height,
                    x=self.webview_x,
                    y=self.webview_y,
                    on_shown=on_shown,
                    on_closed=on_closed
                )
                
                # 使用事件循环启动webview
                webview.start()

            def open_in_webview(self, url):
                """在webview中打开URL"""
                # 获取屏幕宽度
                screen_width = self.winfo_screenwidth()
                screen_height = self.winfo_screenheight()
                
                # 设置引导窗口位置（靠左）
                guide_width = 600
                guide_height = 680
                guide_x = 50
                guide_y = (screen_height - guide_height) // 2
                self.geometry(f"{guide_width}x{guide_height}+{guide_x}+{guide_y}")
                
                # 存储webview窗口位置信息
                self.webview_width = 1024
                self.webview_height = 800
                self.webview_x = guide_x + guide_width + 30
                self.webview_y = (screen_height - self.webview_height) // 2
                
                # 改为使用webbrowser直接打开URL
                webbrowser.open(url)

            def copy_to_clipboard(self, text):
                pyperclip.copy(text)
                messagebox.showinfo("提示", "已复制到剪贴板！")
                
            def save_credentials(self):
                client_id = self.client_id.get().strip()
                client_secret = self.client_secret.get().strip()
                
                if not client_id or not client_secret:
                    messagebox.showerror("错误", "请填写所有字段！")
                    return
                    
                self.credentials = (client_id, client_secret)
                self.destroy()
        
        # 创建并显示设置窗口
        root = tk.Tk()
        root.withdraw()  # 隐藏主窗口
        setup_window = SetupWindow()  # 直接创建SetupWindow实例
        
        # 等待窗口关闭
        root.wait_window(setup_window)
        root.destroy()
        
        if setup_window.credentials:
            self.github_auth.CLIENT_ID = setup_window.credentials[0]
            self.github_auth.CLIENT_SECRET = setup_window.credentials[1]
            self.github_auth.save_config()
            return True
        return False

    def _get_github_token(self):
        """通过OAuth流程获取GitHub访问令牌"""
        # 生成随机state用于防止CSRF攻击
        state = secrets.token_hex(16)
        auth_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={self.github_auth.CLIENT_ID}"
            f"&scope=repo"  # 请求仓库访问权限
            f"&state={state}"
            f"&redirect_uri=http://localhost:9826/callback"  # 显式指定回调地址
        )
        
        # 创建一个事件用于同步
        auth_complete = threading.Event()
        server_error = None
        auth_data = {'code': None, 'state': None}
        
        def run_server():
            try:
                server = HTTPServer(('localhost', 9826), OAuthCallbackHandler)
                server.oauth_code = None
                server.oauth_state = None
                server.timeout = 1  # 设置超时时间，使服务器可以优雅退出
                
                while not auth_complete.is_set():
                    server.handle_request()
                    if server.oauth_code and server.oauth_state:
                        auth_data['code'] = server.oauth_code
                        auth_data['state'] = server.oauth_state
                        auth_complete.set()
                        
            except Exception as e:
                nonlocal server_error
                server_error = str(e)
                auth_complete.set()
            finally:
                server.server_close()
        
        # 在新线程中启动服务器
        server_thread = threading.Thread(target=run_server)
        server_thread.daemon = True
        server_thread.start()
        
        try:
            # 创建登录窗口
            window = webview.create_window('GitHub登录', auth_url, width=800, height=600)
            
            # 启动webview（修复lambda参数问题）
            webview.start(func=lambda: auth_complete.wait())
            
            # 检查是否有错误发生
            if server_error:
                raise ValueError(f"服务器错误: {server_error}")
            
            # 验证授权结果
            if not auth_data['code'] or auth_data['state'] != state:
                raise ValueError("GitHub授权失败或已取消")
            
            # 使用授权码获取访问令牌
            response = requests.post(
                'https://github.com/login/oauth/access_token',
                data={
                    'client_id': self.github_auth.CLIENT_ID,
                    'client_secret': self.github_auth.CLIENT_SECRET,
                    'code': auth_data['code'],
                    'redirect_uri': 'http://localhost:9826/callback',
                    'state': state
                },
                headers={'Accept': 'application/json'}
            )
            
            if response.status_code != 200:
                raise ValueError("获取访问令牌失败")
            
            token_data = response.json()
            if 'access_token' not in token_data:
                raise ValueError("未收到访问令牌")
            
            # 保存令牌到凭据管理器
            self.github_auth.store_token(token_data['access_token'])
            print("GitHub授权成功！")
            return True
            
        except Exception as e:
            print(f"OAuth流程失败: {str(e)}")
            return False
        finally:
            auth_complete.set()  # 确保服务器线程能够退出

    def github_login(self):
        """GitHub登录"""
        try:
            # 首先尝试使用已存储的令牌
            stored_token = self.github_auth.get_stored_token()
            if stored_token and self.github_auth.verify_token(stored_token):
                print("已使用存储的令牌自动登录！")
                return True
                
            # 检查是否已配置Client ID和Secret
            if not self.github_auth.CLIENT_ID or not self.github_auth.CLIENT_SECRET:
                print("未配置GitHub应用凭据，正在启动设置向导...")
                if not self.setup_github_app():
                    print("GitHub应用设置失败！")
                    return False
                print("应用配置已保存，现在开始登录...")
            
            # 进行新的OAuth认证
            if self._get_github_token():
                print("GitHub登录成功！令牌已安全保存，下次将自动登录。")
                return True
            return False
        except Exception as e:
            print(f"GitHub登录失败：{str(e)}")
            return False

    def github_logout(self):
        """GitHub退出登录"""
        try:
            self.github_auth.store_token("")  # 清除存储的令牌
            print("已成功退出GitHub账户！")
            return True
        except Exception as e:
            print(f"退出登录失败：{str(e)}")
            return False

    def show_github_menu(self):
        """显示GitHub账户管理菜单"""
        while True:
            token = self.github_auth.get_stored_token()
            is_logged_in = token and self.github_auth.verify_token(token)
            has_config = bool(self.github_auth.CLIENT_ID and self.github_auth.CLIENT_SECRET)
            
            print("\n=== GitHub账户管理 ===")
            print(f"应用配置状态: {'已配置' if has_config else '未配置'}")
            print(f"登录状态: {'已登录' if is_logged_in else '未登录'}")
            if is_logged_in:
                print("提示：令牌已安全保存，下次启动程序将自动登录")
            
            print("\n1. " + ("重新登录" if is_logged_in else "登录用户"))
            print("2. 退出登录")
            print("3. " + ("重新配置" if has_config else "配置") + "GitHub应用")
            print("0. 返回上级菜单")
            
            choice = input("\n请输入选项（0-3）：")
            
            if choice == "1":
                self.github_login()
            elif choice == "2":
                self.github_logout()
            elif choice == "3":
                self.setup_github_app()
            elif choice == "0":
                break
            else:
                print("无效的选项，请重新输入！")

def main():
    """主程序入口"""
    font_manager = FontManager()
    
    while True:
        print("\n=== 字体管理器 ===")
        print("1. GitHub账户管理")
        print("0. 退出程序")
        
        choice = input("\n请输入选项（0-1）：")
        
        if choice == "1":
            font_manager.show_github_menu()
        elif choice == "0":
            print("感谢使用，再见！")
            break
        else:
            print("无效的选项，请重新输入！")

if __name__ == "__main__":
    main()
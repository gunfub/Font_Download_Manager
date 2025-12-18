import os
from github_auth import GitHubManager
from font_manager import run_gui
import time
import ctypes
from colorama import Fore, Back, Style, init
import sys

# 检查当前程序是否拥有管理员权限，如果没有则尝试申请
def run_as_admin():
    print("管理员权限核验", end=' ')
    print("....................................................", end=' ')
    time.sleep(0.2)

    if ctypes.windll.shell32.IsUserAnAdmin():
        print(Fore.GREEN + "通过\n\n")
        time.sleep(0.4)
        return True  # 已是管理员
    
    else:
        print(Fore.RED + "失败")
        print("权限不足，将在3秒后尝试申请管理员权限\n\n")
        time.sleep(3)  # 等待3秒
        
        # 重新以管理员运行
        params = ' '.join([sys.executable] + sys.argv)
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1
            )
            # 退出当前没有管理员权限的进程
            sys.exit(0)
        except Exception as e:
            print(f"请求管理员权限时出错: {e}")
            return False


class FontManager:
    def __init__(self):
        self.github_manager = GitHubManager()
    
    def show_menu(self):
        while True:
            os.system('cls')
            print("\n=== 字体管理器 ===")
            print("1. GitHub账户管理")
            print("2. 字体管理")
            print("0. 退出程序")
            
            choice = input("\n请输入选项（0-2）：")
            
            if choice == "1":
                os.system('cls')
                self.github_manager.show_menu()

            elif choice == "2":
                os.system('cls')
                run_gui()

            elif choice == "0":
                os.system('cls')
                print("感谢使用，再见！")
                time.sleep(0.5)
                break
                
            else:
                print("无效的选项，请重新输入！")

def main():
    run_as_admin()
    font_manager = FontManager()
    font_manager.show_menu()

if __name__ == "__main__":
    main()

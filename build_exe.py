import os
import shutil
import subprocess
import sys
from pathlib import Path

def build_exe():
    """
    使用 PyInstaller 将项目打包成单个 EXE 文件，并复制必要的配置文件和目录。
    """
    # --- 配置区域 ---
    main_script = "ocr_gui.py"  # 主程序脚本
    app_name = "OCR图片处理工具" # 生成的 EXE 名称
    
    # 需要跟随 EXE 的文件
    required_files = [
        "brandCode.csv",
        "refToPartnum.xml"
    ]
    
    # 需要跟随 EXE 的目录
    required_dirs = [
        "refToPN",
        "refToPartnum-OK"
    ]
    
    # ----------------

    print("=== 开始打包流程 ===")

    # 1. 检查并安装 PyInstaller
    try:
        import PyInstaller
        print(f"检测到 PyInstaller 版本: {PyInstaller.__version__}")
    except ImportError:
        print("未检测到 PyInstaller，正在为您安装...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        except Exception as e:
            print(f"安装失败: {e}")
            return

    # 2. 清理之前的构建目录
    for folder in ['build', 'dist']:
        if os.path.exists(folder):
            print(f"清理旧的 {folder} 目录...")
            shutil.rmtree(folder)

    # 3. 运行 PyInstaller
    # --onefile: 打包成单个文件
    # --windowed: 运行时不显示控制台窗口 (GUI程序必备)
    # --clean: 打包前清理缓存
    # --name: 指定输出名称
    print(f"\n正在调用 PyInstaller 打包 {main_script}...")
    
    pyinstaller_cmd = [
        "pyinstaller",
        "--onefile",
        "--windowed",
        "--clean",
        f"--name={app_name}",
        main_script
    ]
    
    try:
        subprocess.run(pyinstaller_cmd, check=True)
        print("\nPyInstaller 打包成功！")
    except subprocess.CalledProcessError as e:
        print(f"\n打包过程中出现错误: {e}")
        return

    # 4. 准备发布目录 (dist 文件夹)
    print("\n正在整理发布文件...")
    dist_path = Path("dist")
    
    # 复制文件
    for file_name in required_files:
        src = Path(file_name)
        if src.exists():
            shutil.copy2(src, dist_path / file_name)
            print(f"  [+] 已复制文件: {file_name}")
        else:
            print(f"  [!] 警告: 找不到必要文件 {file_name}")

    # 复制/创建目录
    for dir_name in required_dirs:
        src_dir = Path(dir_name)
        dest_dir = dist_path / dir_name
        
        if src_dir.exists():
            # 这里选择递归复制，如果只想创建空目录，可以改为 os.makedirs
            # 为了方便用户，我们连带现有的配置一起复制
            shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
            print(f"  [+] 已复制目录: {dir_name}")
        else:
            # 如果源目录不存在，直接创建一个空的
            os.makedirs(dest_dir, exist_ok=True)
            print(f"  [*] 已创建空目录: {dir_name}")

    print("\n" + "="*30)
    print("打包完成！")
    print(f"发布文件夹路径: {dist_path.absolute()}")
    print(f"可执行文件: {app_name}.exe")
    print("="*30)
    print("提示: 发布时请务必将 dist 文件夹内的所有内容一起提供给用户。")

if __name__ == "__main__":
    build_exe()

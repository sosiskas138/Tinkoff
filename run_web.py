#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Кроссплатформенный скрипт для запуска веб-интерфейса
Работает на Windows, Linux и macOS
"""
import os
import sys
import subprocess
import platform

def check_python_version():
    """Проверяет версию Python"""
    if sys.version_info < (3, 8):
        print("[ERROR] Python 3.8 or higher is required")
        print(f"[INFO] Current version: {sys.version}")
        sys.exit(1)

def check_and_install_dependencies():
    """Проверяет и устанавливает зависимости"""
    required_packages = ['flask', 'plotly']
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)
    
    if missing:
        print("[INFO] Missing libraries detected. Installing...")
        print(f"[INFO] Installing: {', '.join(missing)}")
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-q", "--upgrade"
            ] + missing)
            print("[SUCCESS] Libraries installed successfully!")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Error installing libraries: {e}")
            print("[INFO] Try installing manually:")
            print(f"   pip install {' '.join(missing)}")
            sys.exit(1)

def check_token():
    """Проверяет наличие токена"""
    token = os.getenv('INVEST_TOKEN')
    if not token:
        print("[ERROR] Environment variable INVEST_TOKEN is not set")
        print()
        if platform.system() == 'Windows':
            print("Set sandbox token with command:")
            print("  set INVEST_TOKEN=your_sandbox_token_here")
            print()
            print("Or via System Settings:")
            print("  Control Panel - System - Environment Variables")
        else:
            print("Set sandbox token with command:")
            print("  export INVEST_TOKEN='your_sandbox_token_here'")
        print()
        input("Press Enter to exit...")
        sys.exit(1)

def main():
    """Основная функция"""
    # Устанавливаем кодировку для Windows
    if platform.system() == 'Windows':
        try:
            # Пытаемся установить UTF-8 для консоли
            os.system('chcp 65001 >nul 2>&1')
        except:
            pass
    
    print("=" * 60)
    print("[INFO] Starting web interface for trading strategies")
    print("=" * 60)
    print()
    
    # Проверки
    check_python_version()
    check_and_install_dependencies()
    check_token()
    
    # Добавляем текущую директорию в PYTHONPATH
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, current_dir)
    os.environ['PYTHONPATH'] = os.environ.get('PYTHONPATH', '') + os.pathsep + current_dir
    
    print()
    print("[INFO] Starting web server...")
    print("[INFO] Open in browser: http://localhost:8080")
    print()
    print("[INFO] Press Ctrl+C to stop")
    print("=" * 60)
    print()
    
    # Импортируем и запускаем веб-приложение
    try:
        import web_app
        # Запускаем Flask сервер
        web_app.app.run(host='0.0.0.0', port=8080, debug=True)
    except KeyboardInterrupt:
        print()
        print("[INFO] Server stopped by user")
    except Exception as e:
        print(f"[ERROR] Error starting server: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)

if __name__ == "__main__":
    main()


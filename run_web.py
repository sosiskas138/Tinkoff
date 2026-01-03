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
        print("❌ Ошибка: требуется Python 3.8 или выше")
        print(f"   Текущая версия: {sys.version}")
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
        print("📦 Обнаружены отсутствующие библиотеки. Установка...")
        print(f"   Устанавливаем: {', '.join(missing)}")
        try:
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-q", "--upgrade"
            ] + missing)
            print("✅ Библиотеки успешно установлены!")
        except subprocess.CalledProcessError as e:
            print(f"❌ Ошибка установки библиотек: {e}")
            print("   Попробуйте установить вручную:")
            print(f"   pip install {' '.join(missing)}")
            sys.exit(1)

def check_token():
    """Проверяет наличие токена"""
    token = os.getenv('INVEST_TOKEN')
    if not token:
        print("❌ Ошибка: переменная окружения INVEST_TOKEN не установлена")
        print()
        if platform.system() == 'Windows':
            print("Установите sandbox токен командой:")
            print("  set INVEST_TOKEN=ваш_sandbox_токен_здесь")
            print()
            print("Или через настройки системы:")
            print("  Панель управления → Система → Переменные среды")
        else:
            print("Установите sandbox токен командой:")
            print("  export INVEST_TOKEN='ваш_sandbox_токен_здесь'")
        print()
        input("Нажмите Enter для выхода...")
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
    print("🚀 Запуск веб-интерфейса для торговых стратегий")
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
    print("🚀 Запуск веб-сервера...")
    print("📊 Откройте в браузере: http://localhost:8080")
    print()
    print("Для остановки нажмите Ctrl+C")
    print("=" * 60)
    print()
    
    # Импортируем и запускаем веб-приложение
    try:
        import web_app
        # Запускаем Flask сервер
        web_app.app.run(host='0.0.0.0', port=8080, debug=True)
    except KeyboardInterrupt:
        print()
        print("👋 Сервер остановлен пользователем")
    except Exception as e:
        print(f"❌ Ошибка при запуске: {e}")
        import traceback
        traceback.print_exc()
        input("\nНажмите Enter для выхода...")
        sys.exit(1)

if __name__ == "__main__":
    main()


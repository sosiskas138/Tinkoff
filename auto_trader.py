#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для автоматической торговли на live данных
Поддерживает Windows и автоматическую установку зависимостей
"""
import os
import sys
import json
import threading
import time
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable
from decimal import Decimal
import logging

# Проверка и установка зависимостей
def _check_and_install_dependencies():
    """Проверяет наличие необходимых библиотек и устанавливает их при необходимости"""
    # Словарь: имя модуля для импорта -> имя пакета для pip
    # None означает, что модуль локальный и не нужно устанавливать из pip
    required_packages = {
        'grpc': 'grpcio',  # Требуется для tinkoff API
        'google.protobuf': 'protobuf',  # Требуется для grpc
        'dateutil': 'python-dateutil',  # Требуется для tinkoff
        'cachetools': 'cachetools',  # Требуется для tinkoff
        'flask': 'flask',
        'plotly': 'plotly',
        'numpy': 'numpy',
        'pandas': 'pandas',
    }
    
    missing_packages = []
    for module_name, package_name in required_packages.items():
        try:
            if module_name == 'google.protobuf':
                __import__('google.protobuf')
            else:
                __import__(module_name)
        except ImportError:
            if package_name:
                missing_packages.append(package_name)
    
    if missing_packages:
        try:
            # Используем ASCII-совместимые сообщения для Windows
            print("[INFO] Missing libraries detected. Installing...")
            print(f"[INFO] Installing: {', '.join(missing_packages)}")
            # Используем pip для установки
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", "-q"
            ] + missing_packages)
            print("[SUCCESS] Libraries installed successfully!")
            print("[INFO] Restart the script to apply changes.")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Error installing libraries: {e}")
            print("[INFO] Try installing manually:")
            print(f"   pip install {' '.join(missing_packages)}")
            # Не останавливаем выполнение, возможно библиотеки уже есть

# Проверяем зависимости при импорте
_check_and_install_dependencies()

from tinkoff.invest.sandbox.client import SandboxClient
from tinkoff.invest import CandleInterval, OrderDirection, OrderType
from tinkoff.invest.utils import quotation_to_decimal, money_to_decimal, decimal_to_quotation, now, MAX_INTERVALS, get_intervals
from strategies_backtest import KAMAStrategy, Candle as BacktestCandle
from strategy_optimizer import AdaptiveStrategy, load_optimized_strategy

logger = logging.getLogger(__name__)


class AutoTrader:
    """
    Автоматический торговец, который торгует на live данных используя обученную стратегию
    """
    
    def __init__(
        self,
        figi: str,
        account_id: str,
        strategy: AdaptiveStrategy,
        token: str,
        interval: CandleInterval = CandleInterval.CANDLE_INTERVAL_HOUR,
        trade_percent: float = 100.0,
        on_signal_callback: Optional[Callable] = None,
    ):
        self.figi = figi
        self.account_id = account_id
        self.strategy = strategy
        self.token = token
        self.interval = interval
        self.trade_percent = trade_percent
        self.on_signal_callback = on_signal_callback
        
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.last_candle_time: Optional[datetime] = None
        self.candles_history: list = []
        self.position = 0  # Текущая позиция (количество лотов)
        self.last_signal = None
        self.logs: list = []  # Логи для веб-интерфейса
        self.max_logs = 1000  # Максимальное количество логов
        
        # Данные для графика
        self.price_history: list = []  # История цен
        self.signals_history: list = []  # История сигналов
        self.equity_history: list = []  # История капитала
        self.max_history_points = 500  # Максимальное количество точек на графике
        
        # Создаем стратегию с оптимальными параметрами
        params = strategy.optimized_params
        self.kama_strategy = KAMAStrategy(
            kama_len=params.kama_len,
            kama_fast=params.kama_fast,
            kama_slow=params.kama_slow,
            entry_mult=params.entry_mult,
            exit_mult=params.exit_mult,
            atr_len=params.atr_len,
            atr_sl_mult=params.atr_sl_mult,
            atr_phase_len=params.atr_phase_len,
            atr_phase_mult=params.atr_phase_mult,
            body_atr_mult=params.body_atr_mult,
        )
    
    def _add_log(self, level: str, message: str):
        """Добавить лог в список"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'level': level,
            'message': message
        }
        self.logs.append(log_entry)
        # Ограничиваем размер списка логов
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)
    
    def start(self):
        """Запустить автоматическую торговлю"""
        if self.is_running:
            logger.warning("AutoTrader уже запущен")
            self._add_log('WARNING', "AutoTrader уже запущен")
            return
        
        self.is_running = True
        self.thread = threading.Thread(target=self._trading_loop, daemon=True)
        self.thread.start()
        logger.info(f"AutoTrader запущен для {self.figi}")
        self._add_log('INFO', f"AutoTrader запущен для {self.figi}")
    
    def stop(self):
        """Остановить автоматическую торговлю"""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info(f"AutoTrader остановлен для {self.figi}")
        self._add_log('INFO', f"AutoTrader остановлен для {self.figi}")
    
    def _trading_loop(self):
        """Основной цикл торговли"""
        with SandboxClient(self.token) as client:
            # Загружаем начальную историю (последние 100 свечей)
            self._load_history(client)
            
            while self.is_running:
                try:
                    # Получаем новые свечи
                    new_candles = self._get_new_candles(client)
                    
                    if new_candles:
                        # Добавляем в историю
                        for candle in new_candles:
                            self.candles_history.append(candle)
                            # Ограничиваем размер истории (последние 500 свечей)
                            if len(self.candles_history) > 500:
                                self.candles_history.pop(0)
                        
                        # Обновляем историю цен для графика
                        if self.candles_history:
                            last_candle = self.candles_history[-1]
                            self._update_price_history(last_candle)
                        
                        # Получаем сигналы стратегии
                        signals = self.kama_strategy.get_signals(self.candles_history)
                        
                        if signals:
                            # Берем последний сигнал
                            last_signal = signals[-1]
                            self.last_signal = last_signal
                            self._add_log('INFO', f"Получен сигнал: {last_signal.get('action')} по цене {last_signal.get('price', 0):.2f}")
                            
                            # Сохраняем сигнал в историю для графика
                            self._add_signal_to_history(last_signal)
                            
                            # Исполняем сигнал
                            self._execute_signal(client, last_signal)
                            
                            # Вызываем callback если есть
                            if self.on_signal_callback:
                                self.on_signal_callback(last_signal)
                        else:
                            self._add_log('DEBUG', f"Новых сигналов нет. Свечей в истории: {len(self.candles_history)}")
                    
                    # Ждем перед следующей итерацией
                    # Для часовых свечей проверяем каждую минуту
                    time.sleep(60)
                    
                except Exception as e:
                    logger.error(f"Ошибка в торговом цикле: {e}", exc_info=True)
                    self._add_log('ERROR', f"Ошибка в торговом цикле: {e}")
                    time.sleep(60)
    
    def _load_history(self, client):
        """Загрузить исторические свечи для инициализации"""
        try:
            to_date = now()
            
            # Определяем максимальный период для данного интервала
            max_period = MAX_INTERVALS.get(self.interval, timedelta(days=7))
            
            # Запрашиваем период, не превышающий максимум
            # Для часовых свечей максимум 1 неделя, для дневных - больше
            if self.interval == CandleInterval.CANDLE_INTERVAL_HOUR:
                from_date = to_date - timedelta(days=7)  # 1 неделя для часовых свечей
            elif self.interval == CandleInterval.CANDLE_INTERVAL_DAY:
                from_date = to_date - timedelta(days=30)  # 30 дней для дневных свечей
            else:
                # Для других интервалов используем максимальный период
                from_date = to_date - max_period
            
            # Если нужен больший период, загружаем частями
            desired_period = timedelta(days=30)  # Желаемый период
            if (to_date - from_date) < desired_period and self.interval == CandleInterval.CANDLE_INTERVAL_DAY:
                # Для дневных свечей можем загрузить больше
                from_date = to_date - desired_period
            
            self.candles_history = []
            
            # Загружаем свечи частями, если период превышает максимум
            if (to_date - from_date) > max_period:
                self._add_log('INFO', f'Загрузка истории частями (период превышает максимум {max_period})')
                for period_from, period_to in get_intervals(self.interval, from_date, to_date):
                    try:
                        candles_response = client.market_data.get_candles(
                            instrument_id=self.figi,
                            from_=period_from,
                            to=period_to,
                            interval=self.interval
                        )
                        for candle in candles_response.candles:
                            self.candles_history.append(BacktestCandle(
                                time=candle.time,
                                open=float(quotation_to_decimal(candle.open)),
                                high=float(quotation_to_decimal(candle.high)),
                                low=float(quotation_to_decimal(candle.low)),
                                close=float(quotation_to_decimal(candle.close)),
                                volume=candle.volume,
                            ))
                    except Exception as e:
                        logger.warning(f"Ошибка загрузки части истории {period_from} - {period_to}: {e}")
                        self._add_log('WARNING', f'Ошибка загрузки части истории: {e}')
                        continue
            else:
                # Загружаем одним запросом
                candles_response = client.market_data.get_candles(
                    instrument_id=self.figi,
                    from_=from_date,
                    to=to_date,
                    interval=self.interval
                )
                
                for candle in candles_response.candles:
                    self.candles_history.append(BacktestCandle(
                        time=candle.time,
                        open=float(quotation_to_decimal(candle.open)),
                        high=float(quotation_to_decimal(candle.high)),
                        low=float(quotation_to_decimal(candle.low)),
                        close=float(quotation_to_decimal(candle.close)),
                        volume=candle.volume,
                    ))
            
            # Сортируем по времени на случай, если загружали частями
            self.candles_history.sort(key=lambda x: x.time)
            
            if self.candles_history:
                self.last_candle_time = self.candles_history[-1].time
                # Инициализируем историю цен для графика
                for candle in self.candles_history:
                    self._update_price_history(candle)
                logger.info(f"Загружено {len(self.candles_history)} исторических свечей")
                self._add_log('INFO', f"Загружено {len(self.candles_history)} исторических свечей")
            else:
                logger.warning("Не удалось загрузить исторические свечи")
                self._add_log('WARNING', "Не удалось загрузить исторические свечи. Будет использована минимальная история.")
                # Загружаем хотя бы последние несколько свечей
                try:
                    from_date_min = to_date - max_period
                    candles_response = client.market_data.get_candles(
                        instrument_id=self.figi,
                        from_=from_date_min,
                        to=to_date,
                        interval=self.interval
                    )
                    for candle in candles_response.candles:
                        self.candles_history.append(BacktestCandle(
                            time=candle.time,
                            open=float(quotation_to_decimal(candle.open)),
                            high=float(quotation_to_decimal(candle.high)),
                            low=float(quotation_to_decimal(candle.low)),
                            close=float(quotation_to_decimal(candle.close)),
                            volume=candle.volume,
                        ))
                    if self.candles_history:
                        self.candles_history.sort(key=lambda x: x.time)
                        self.last_candle_time = self.candles_history[-1].time
                        self._add_log('INFO', f"Загружено минимальная история: {len(self.candles_history)} свечей")
                except Exception as e2:
                    logger.error(f"Критическая ошибка загрузки минимальной истории: {e2}")
                    self._add_log('ERROR', f"Критическая ошибка: {e2}")
        
        except Exception as e:
            logger.error(f"Ошибка загрузки истории: {e}", exc_info=True)
            self._add_log('ERROR', f"Ошибка загрузки истории: {e}")
    
    def _get_new_candles(self, client) -> list:
        """Получить новые свечи с момента last_candle_time"""
        try:
            to_date = now()
            from_date = self.last_candle_time if self.last_candle_time else (to_date - timedelta(days=1))
            
            candles_response = client.market_data.get_candles(
                instrument_id=self.figi,
                from_=from_date,
                to=to_date,
                interval=self.interval
            )
            
            new_candles = []
            for candle in candles_response.candles:
                candle_time = candle.time
                if not self.last_candle_time or candle_time > self.last_candle_time:
                    new_candles.append(BacktestCandle(
                        time=candle_time,
                        open=float(quotation_to_decimal(candle.open)),
                        high=float(quotation_to_decimal(candle.high)),
                        low=float(quotation_to_decimal(candle.low)),
                        close=float(quotation_to_decimal(candle.close)),
                        volume=candle.volume,
                    ))
                    if not self.last_candle_time or candle_time > self.last_candle_time:
                        self.last_candle_time = candle_time
            
            return new_candles
        
        except Exception as e:
            logger.error(f"Ошибка получения новых свечей: {e}", exc_info=True)
            return []
    
    def _execute_signal(self, client, signal: dict):
        """Исполнить торговый сигнал"""
        try:
            action = signal.get('action')
            
            if action == 'BUY' and self.position == 0:
                # Покупка
                price = signal.get('price')
                if not price:
                    return
                
                # Получаем баланс
                positions = client.operations.get_positions(account_id=self.account_id)
                available_money = 0
                for money in positions.money:
                    if money.currency.lower() == 'rub':
                        available_money = float(money_to_decimal(money))
                        break
                
                if available_money < price * 10:  # Минимум на 10 лотов
                    logger.warning(f"Недостаточно средств для покупки: {available_money}")
                    self._add_log('WARNING', f"Недостаточно средств для покупки: {available_money:.2f} ₽")
                    return
                
                # Рассчитываем количество лотов
                cost = available_money * (self.trade_percent / 100.0)
                quantity = int(cost / price)
                
                if quantity > 0:
                    # Получаем информацию об инструменте для лота
                    from tinkoff.invest import InstrumentIdType
                    instrument = client.instruments.get_instrument_by(
                        id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI,
                        id=self.figi
                    )
                    lot_size = instrument.instrument.lot if instrument else 1
                    quantity_lots = max(1, quantity // lot_size)
                    
                    # Создаем ордер
                    from uuid import uuid4
                    response = client.orders.post_order(
                        account_id=self.account_id,
                        instrument_id=self.figi,
                        quantity=quantity_lots,
                        direction=OrderDirection.ORDER_DIRECTION_BUY,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                        order_id=str(uuid4()),
                    )
                    
                    self.position = quantity_lots * lot_size
                    logger.info(f"Создан ордер на покупку: {quantity_lots} лотов, order_id={response.order_id}")
                    self._add_log('INFO', f"Создан ордер на покупку: {quantity_lots} лотов по цене {price:.2f}, order_id={response.order_id}")
            
            elif action == 'SELL' and self.position > 0:
                # Продажа
                price = signal.get('price')
                if not price:
                    return
                
                # Получаем текущую позицию
                positions = client.operations.get_positions(account_id=self.account_id)
                available_quantity = 0
                for sec in positions.securities:
                    if sec.figi == self.figi:
                        from tinkoff.invest.utils import quotation_to_decimal
                        available_quantity = float(quotation_to_decimal(sec.balance))
                        blocked = float(quotation_to_decimal(sec.blocked)) if sec.blocked else 0
                        available_quantity = available_quantity - blocked
                        break
                
                if available_quantity <= 0:
                    logger.warning(f"Нет позиции для продажи")
                    self._add_log('WARNING', "Нет позиции для продажи")
                    self.position = 0
                    return
                
                # Получаем информацию об инструменте для лота
                from tinkoff.invest import InstrumentIdType
                instrument = client.instruments.get_instrument_by(
                    id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI,
                    id=self.figi
                )
                lot_size = instrument.instrument.lot if instrument else 1
                quantity_lots = int(available_quantity / lot_size)
                
                if quantity_lots > 0:
                    # Создаем ордер
                    from uuid import uuid4
                    response = client.orders.post_order(
                        account_id=self.account_id,
                        instrument_id=self.figi,
                        quantity=quantity_lots,
                        direction=OrderDirection.ORDER_DIRECTION_SELL,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                        order_id=str(uuid4()),
                    )
                    
                    self.position = 0
                    logger.info(f"Создан ордер на продажу: {quantity_lots} лотов, order_id={response.order_id}")
                    self._add_log('INFO', f"Создан ордер на продажу: {quantity_lots} лотов по цене {price:.2f}, order_id={response.order_id}")
        
        except Exception as e:
            logger.error(f"Ошибка исполнения сигнала: {e}", exc_info=True)
            self._add_log('ERROR', f"Ошибка исполнения сигнала: {e}")
    
    def get_status(self) -> dict:
        """Получить текущий статус торговца"""
        return {
            'is_running': self.is_running,
            'figi': self.figi,
            'position': self.position,
            'last_signal': self.last_signal,
            'candles_count': len(self.candles_history),
        }
    
    def get_logs(self, limit: int = 100) -> list:
        """Получить последние логи"""
        return self.logs[-limit:] if limit else self.logs
    
    def _update_price_history(self, candle: BacktestCandle):
        """Обновить историю цен для графика"""
        price_point = {
            'time': candle.time.isoformat() if hasattr(candle.time, 'isoformat') else str(candle.time),
            'open': candle.open,
            'high': candle.high,
            'low': candle.low,
            'close': candle.close,
            'volume': candle.volume,
        }
        self.price_history.append(price_point)
        
        # Ограничиваем размер истории
        if len(self.price_history) > self.max_history_points:
            self.price_history.pop(0)
    
    def _add_signal_to_history(self, signal: dict):
        """Добавить сигнал в историю для графика"""
        signal_time = signal.get('time')
        if hasattr(signal_time, 'isoformat'):
            time_str = signal_time.isoformat()
        else:
            time_str = str(signal_time)
        
        signal_point = {
            'time': time_str,
            'action': signal.get('action'),
            'price': signal.get('price', 0),
            'reason': signal.get('reason', ''),
        }
        self.signals_history.append(signal_point)
        
        # Ограничиваем размер истории
        if len(self.signals_history) > self.max_history_points:
            self.signals_history.pop(0)
    
    def _update_equity_history(self, current_equity: float):
        """Обновить историю капитала"""
        equity_point = {
            'time': datetime.now().isoformat(),
            'equity': current_equity,
        }
        self.equity_history.append(equity_point)
        
        # Ограничиваем размер истории
        if len(self.equity_history) > self.max_history_points:
            self.equity_history.pop(0)
    
    def get_chart_data(self) -> dict:
        """Получить данные для графика"""
        # Конвертируем last_signal для JSON
        last_signal_dict = None
        if self.last_signal:
            last_signal_dict = {
                'action': self.last_signal.get('action'),
                'price': self.last_signal.get('price', 0),
                'time': self.last_signal.get('time').isoformat() if hasattr(self.last_signal.get('time'), 'isoformat') else str(self.last_signal.get('time')),
                'reason': self.last_signal.get('reason', ''),
            }
        
        return {
            'price_history': self.price_history[-200:],  # Последние 200 точек
            'signals_history': self.signals_history,
            'equity_history': self.equity_history[-200:],
            'current_position': self.position,
            'last_signal': last_signal_dict,
            'candles_count': len(self.candles_history),
        }


# Глобальный словарь активных трейдеров
_active_traders: Dict[str, AutoTrader] = {}


def start_auto_trader(figi: str, account_id: str, strategy_file: str, token: str, interval: CandleInterval, trade_percent: float) -> AutoTrader:
    """Запустить автоматическую торговлю"""
    strategy = load_optimized_strategy(strategy_file)
    
    trader = AutoTrader(
        figi=figi,
        account_id=account_id,
        strategy=strategy,
        token=token,
        interval=interval,
        trade_percent=trade_percent,
    )
    
    key = f"{figi}_{account_id}"
    if key in _active_traders:
        _active_traders[key].stop()
    
    _active_traders[key] = trader
    trader.start()
    
    return trader


def stop_auto_trader(figi: str, account_id: str):
    """Остановить автоматическую торговлю"""
    key = f"{figi}_{account_id}"
    if key in _active_traders:
        _active_traders[key].stop()
        del _active_traders[key]
        return True
    return False


def get_trader_status(figi: str, account_id: str) -> Optional[dict]:
    """Получить статус торговца"""
    key = f"{figi}_{account_id}"
    if key in _active_traders:
        return _active_traders[key].get_status()
    return None


def get_trader_logs(figi: str, account_id: str, limit: int = 100) -> Optional[list]:
    """Получить логи торговца"""
    key = f"{figi}_{account_id}"
    if key in _active_traders:
        return _active_traders[key].get_logs(limit=limit)
    return None


def get_trader_chart_data(figi: str, account_id: str) -> Optional[dict]:
    """Получить данные для графика торговца"""
    key = f"{figi}_{account_id}"
    if key in _active_traders:
        return _active_traders[key].get_chart_data()
    return None


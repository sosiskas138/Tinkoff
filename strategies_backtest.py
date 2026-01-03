#!/usr/bin/env python3
"""
Модуль для бэктестинга торговых стратегий
"""
import numpy as np
from decimal import Decimal
from typing import List, Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Candle:
    """Свеча для бэктестинга"""
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class Trade:
    """Сделка"""
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    quantity: int
    direction: str  # 'LONG' or 'SHORT'
    profit: Optional[float] = None
    profit_pct: Optional[float] = None


@dataclass
class BacktestResult:
    """Результат бэктестинга"""
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_profit: float
    total_profit_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trades: List[Trade]
    equity_curve: List[float]
    final_balance: float


def calculate_kama(prices: np.ndarray, length: int = 21, fast: int = 2, slow: int = 20) -> np.ndarray:
    """
    Рассчитать KAMA (Kaufman Adaptive Moving Average)
    
    Args:
        prices: массив цен (close)
        length: период KAMA
        fast: быстрый период
        slow: медленный период
    
    Returns:
        массив значений KAMA
    """
    kama = np.zeros(len(prices))
    kama[:length] = prices[:length]  # Начальное значение
    
    for i in range(length, len(prices)):
        # Изменение за период
        change = abs(prices[i] - prices[i - length])
        # Волатильность (сумма изменений)
        volatility = np.sum(np.abs(np.diff(prices[i - length:i + 1])))
        # Efficiency Ratio
        er = change / volatility if volatility != 0 else 0
        # Smoothing Constant
        fast_sc = 2.0 / (fast + 1)
        slow_sc = 2.0 / (slow + 1)
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        # KAMA
        kama[i] = sc * prices[i] + (1 - sc) * kama[i - 1]
    
    return kama


def calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    """
    Рассчитать ATR (Average True Range)
    
    Args:
        high: массив максимальных цен
        low: массив минимальных цен
        close: массив цен закрытия
        length: период ATR
    
    Returns:
        массив значений ATR
    """
    tr = np.zeros(len(high))
    tr[0] = high[0] - low[0]
    
    for i in range(1, len(high)):
        tr1 = high[i] - low[i]
        tr2 = abs(high[i] - close[i - 1])
        tr3 = abs(low[i] - close[i - 1])
        tr[i] = max(tr1, tr2, tr3)
    
    atr = np.zeros(len(high))
    atr[:length] = np.nan
    
    # Первое значение ATR - простая средняя TR
    atr[length] = np.mean(tr[1:length + 1])
    
    # Остальные значения - экспоненциальная средняя
    for i in range(length + 1, len(high)):
        atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
    
    return atr


class KAMAStrategy:
    """
    KAMA-Core стратегия из PineScript примера
    """
    
    def __init__(
        self,
        kama_len: int = 21,
        kama_fast: int = 2,
        kama_slow: int = 20,
        entry_mult: float = 1.6,
        exit_mult: float = 0.8,
        atr_len: int = 14,
        atr_sl_mult: float = 2.2,
        atr_phase_len: int = 50,
        atr_phase_mult: float = 1.0,
        body_atr_mult: float = 0.5,
    ):
        self.kama_len = kama_len
        self.kama_fast = kama_fast
        self.kama_slow = kama_slow
        self.entry_mult = entry_mult
        self.exit_mult = exit_mult
        self.atr_len = atr_len
        self.atr_sl_mult = atr_sl_mult
        self.atr_phase_len = atr_phase_len
        self.atr_phase_mult = atr_phase_mult
        self.body_atr_mult = body_atr_mult
        
        self.position = 0  # 0 = нет позиции, >0 = лонг
        self.entry_price = 0.0
        self.sl_level = 0.0
        
    def calculate_indicators(self, candles: List[Candle]) -> Dict[str, np.ndarray]:
        """Рассчитать все индикаторы для стратегии"""
        closes = np.array([c.close for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])
        opens = np.array([c.open for c in candles])
        
        # KAMA
        kama = calculate_kama(closes, self.kama_len, self.kama_fast, self.kama_slow)
        kama_delta = np.zeros(len(kama))
        kama_delta[0] = 0
        kama_delta[1:] = np.diff(kama)
        
        # ATR
        atr = calculate_atr(highs, lows, closes, self.atr_len)
        
        # ATR Phase (SMA of ATR)
        atr_sma = np.full(len(atr), np.nan)
        for i in range(self.atr_phase_len, len(atr)):
            atr_sma[i] = np.nanmean(atr[i - self.atr_phase_len:i])
        
        atr_phase_threshold = atr_sma * self.atr_phase_mult
        in_vol_phase = atr > atr_phase_threshold
        
        # Candle body
        body = np.abs(closes - opens)
        is_bull_strong = (closes > opens) & (body > atr * self.body_atr_mult)
        
        # KAMA Delta filters
        kama_delta_stdev = np.full(len(kama_delta), np.nan)
        for i in range(self.kama_len, len(kama_delta)):
            kama_delta_stdev[i] = np.std(kama_delta[i - self.kama_len:i])
        
        entry_filter = self.entry_mult * kama_delta_stdev
        exit_filter = self.exit_mult * kama_delta_stdev
        
        # SL Level
        sl_level = closes - atr * self.atr_sl_mult
        
        return {
            'kama': kama,
            'kama_delta': kama_delta,
            'atr': atr,
            'atr_sma': atr_sma,
            'in_vol_phase': in_vol_phase,
            'is_bull_strong': is_bull_strong,
            'entry_filter': entry_filter,
            'exit_filter': exit_filter,
            'sl_level': sl_level,
            'closes': closes,
            'highs': highs,
            'lows': lows,
            'opens': opens,
        }
    
    def get_signals(self, candles: List[Candle]) -> List[Dict[str, Any]]:
        """
        Получить сигналы стратегии
        
        Returns:
            список сигналов: {'action': 'BUY'|'SELL'|None, 'price': float, 'time': datetime, 'sl': float}
        """
        if len(candles) < max(self.kama_len, self.atr_len, self.atr_phase_len) + 1:
            return []
        
        indicators = self.calculate_indicators(candles)
        
        signals = []
        position = 0
        entry_price = 0.0
        sl_level = 0.0
        
        for i in range(max(self.kama_len, self.atr_phase_len), len(candles)):
            kama_delta = indicators['kama_delta'][i]
            entry_filter = indicators['entry_filter'][i]
            exit_filter = indicators['exit_filter'][i]
            in_vol_phase = bool(indicators['in_vol_phase'][i]) if not np.isnan(indicators['in_vol_phase'][i]) else False
            is_bull_strong = bool(indicators['is_bull_strong'][i])
            close = indicators['closes'][i]
            low = indicators['lows'][i]
            current_sl = indicators['sl_level'][i]
            
            # Вход в позицию
            if position == 0:
                if np.isnan(kama_delta) or np.isnan(entry_filter):
                    continue
                long_entry_core = kama_delta > 0 and kama_delta > entry_filter
                long_entry = long_entry_core and in_vol_phase and is_bull_strong
                
                if long_entry:
                    position = 1
                    entry_price = close
                    sl_level = current_sl if not np.isnan(current_sl) else close * 0.98
                    signals.append({
                        'action': 'BUY',
                        'price': close,
                        'time': candles[i].time,
                        'sl': sl_level,
                        'indicators': {
                            'kama': indicators['kama'][i],
                            'kama_delta': kama_delta,
                            'atr': indicators['atr'][i],
                            'in_vol_phase': in_vol_phase,
                        }
                    })
            
            # Выход из позиции
            elif position > 0:
                # Проверка SL
                if low <= sl_level:
                    signals.append({
                        'action': 'SELL',
                        'price': sl_level,
                        'time': candles[i].time,
                        'reason': 'SL',
                        'entry_price': entry_price,
                        'profit': sl_level - entry_price,
                        'profit_pct': (sl_level / entry_price - 1) * 100,
                    })
                    position = 0
                    entry_price = 0.0
                    sl_level = 0.0
                # Проверка выхода по фильтру
                elif not np.isnan(kama_delta) and not np.isnan(exit_filter) and kama_delta < 0 and abs(kama_delta) > exit_filter:
                    signals.append({
                        'action': 'SELL',
                        'price': close,
                        'time': candles[i].time,
                        'reason': 'EXIT_FILTER',
                        'entry_price': entry_price,
                        'profit': close - entry_price,
                        'profit_pct': (close / entry_price - 1) * 100,
                    })
                    position = 0
                    entry_price = 0.0
                    sl_level = 0.0
                else:
                    # Обновляем SL если он выше текущего
                    if not np.isnan(current_sl) and current_sl > sl_level:
                        sl_level = current_sl
        
        # Если позиция открыта в конце, закрываем по последней цене
        if position > 0 and len(candles) > 0:
            last_candle = candles[-1]
            signals.append({
                'action': 'SELL',
                'price': last_candle.close,
                'time': last_candle.time,
                'reason': 'END',
                'entry_price': entry_price,
                'profit': last_candle.close - entry_price,
                'profit_pct': (last_candle.close / entry_price - 1) * 100,
            })
        
        return signals


def backtest_strategy(
    candles: List[Candle],
    strategy: KAMAStrategy,
    initial_balance: float = 100000.0,
    commission: float = 0.05,  # 0.05% комиссия
    use_percent_of_equity: bool = True,
    equity_percent: float = 100.0,  # 100% от баланса
) -> BacktestResult:
    """
    Бэктест стратегии
    
    Args:
        candles: список свечей
        strategy: стратегия для тестирования
        initial_balance: начальный баланс
        commission: комиссия в процентах (0.05 = 0.05%)
        use_percent_of_equity: использовать процент от баланса
        equity_percent: процент от баланса для сделки
    
    Returns:
        результат бэктестинга
    """
    signals = strategy.get_signals(candles)
    
    balance = initial_balance
    position = 0  # количество акций
    entry_price = 0.0
    trades = []
    equity_curve = [balance]
    
    for signal in signals:
        if signal['action'] == 'BUY':
            if position == 0:  # Открываем позицию
                if use_percent_of_equity:
                    cost = balance * (equity_percent / 100.0)
                    quantity = int(cost / signal['price'])
                else:
                    quantity = 1  # По умолчанию 1 лот
                
                if quantity > 0:
                    total_cost = quantity * signal['price']
                    commission_cost = total_cost * (commission / 100.0)
                    
                    if total_cost + commission_cost <= balance:
                        balance -= (total_cost + commission_cost)
                        position = quantity
                        entry_price = signal['price']
                        
                        trades.append(Trade(
                            entry_time=signal['time'],
                            exit_time=None,
                            entry_price=entry_price,
                            exit_price=None,
                            quantity=quantity,
                            direction='LONG',
                        ))
        
        elif signal['action'] == 'SELL':
            if position > 0:  # Закрываем позицию
                revenue = position * signal['price']
                commission_cost = revenue * (commission / 100.0)
                balance += (revenue - commission_cost)
                
                profit = revenue - commission_cost - (position * entry_price) - (position * entry_price * commission / 100.0)
                profit_pct = (profit / (position * entry_price)) * 100.0
                
                if trades:
                    last_trade = trades[-1]
                    last_trade.exit_time = signal['time']
                    last_trade.exit_price = signal['price']
                    last_trade.profit = profit
                    last_trade.profit_pct = profit_pct
                
                position = 0
                entry_price = 0.0
        
        # Обновляем кривую капитала
        current_equity = balance
        if position > 0 and len(candles) > 0:
            # Находим текущую цену
            current_price = candles[-1].close
            current_equity = balance + position * current_price
        
        equity_curve.append(current_equity)
    
    # Закрываем последнюю позицию если есть
    if position > 0 and len(candles) > 0:
        last_price = candles[-1].close
        revenue = position * last_price
        commission_cost = revenue * (commission / 100.0)
        balance += (revenue - commission_cost)
        
        profit = revenue - commission_cost - (position * entry_price) - (position * entry_price * commission / 100.0)
        profit_pct = (profit / (position * entry_price)) * 100.0
        
        if trades:
            last_trade = trades[-1]
            last_trade.exit_time = candles[-1].time
            last_trade.exit_price = last_price
            last_trade.profit = profit
            last_trade.profit_pct = profit_pct
    
    # Расчет статистики
    completed_trades = [t for t in trades if t.exit_time is not None]
    winning_trades = [t for t in completed_trades if t.profit and t.profit > 0]
    losing_trades = [t for t in completed_trades if t.profit and t.profit <= 0]
    
    total_profit = balance - initial_balance
    total_profit_pct = (balance / initial_balance - 1) * 100.0
    
    # Max Drawdown
    equity_array = np.array(equity_curve)
    running_max = np.maximum.accumulate(equity_array)
    drawdown = (equity_array - running_max) / running_max * 100
    max_drawdown_pct = np.min(drawdown)
    max_drawdown = initial_balance * (max_drawdown_pct / 100.0)
    
    # Sharpe Ratio (упрощенный)
    if len(equity_curve) > 1:
        returns = np.diff(equity_curve) / equity_curve[:-1] * 100
        sharpe_ratio = np.mean(returns) / np.std(returns) if np.std(returns) > 0 else 0.0
    else:
        sharpe_ratio = 0.0
    
    return BacktestResult(
        total_trades=len(completed_trades),
        winning_trades=len(winning_trades),
        losing_trades=len(losing_trades),
        total_profit=total_profit,
        total_profit_pct=total_profit_pct,
        max_drawdown=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        trades=completed_trades,
        equity_curve=equity_curve,
        final_balance=balance,
    )


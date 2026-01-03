#!/usr/bin/env python3
"""
Модуль для оптимизации и обучения торговых стратегий
"""
import numpy as np
from decimal import Decimal
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import json
from strategies_backtest import KAMAStrategy, backtest_strategy, Candle, BacktestResult


@dataclass
class OptimizedParams:
    """Оптимизированные параметры стратегии"""
    kama_len: int
    kama_fast: int
    kama_slow: int
    entry_mult: float
    exit_mult: float
    atr_len: int
    atr_sl_mult: float
    atr_phase_len: int
    atr_phase_mult: float
    body_atr_mult: float
    fitness_score: float  # Оценка качества (например, Sharpe Ratio * profit_pct)
    total_profit_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    total_trades: int


class StrategyOptimizer:
    """
    Оптимизатор параметров стратегии на исторических данных
    """
    
    def __init__(
        self,
        candles: List[Candle],
        initial_balance: float = 100000.0,
        commission: float = 0.05,
    ):
        self.candles = candles
        self.initial_balance = initial_balance
        self.commission = commission
        
        # Разбиваем данные на обучающую и валидационную выборки (80/20)
        split_index = int(len(candles) * 0.8)
        self.train_candles = candles[:split_index]
        self.val_candles = candles[split_index:]
    
    def calculate_fitness(
        self,
        result: BacktestResult,
        weight_profit: float = 1.0,
        weight_sharpe: float = 0.5,
        weight_winrate: float = 0.3,
        penalty_drawdown: float = 0.5,
    ) -> float:
        """
        Рассчитать фитнес-функцию для оценки качества стратегии
        
        Args:
            result: результат бэктеста
            weight_profit: вес прибыли
            weight_sharpe: вес Sharpe Ratio
            weight_winrate: вес процента побед
            penalty_drawdown: штраф за просадку
        
        Returns:
            оценка фитнеса
        """
        profit_score = result.total_profit_pct / 100.0 if result.total_profit_pct > 0 else 0
        sharpe_score = max(0, result.sharpe_ratio) / 3.0  # Нормализуем Sharpe (обычно до 3)
        winrate = (result.winning_trades / result.total_trades) if result.total_trades > 0 else 0
        drawdown_penalty = abs(result.max_drawdown_pct) / 100.0
        
        # Базовый фитнес
        fitness = (
            weight_profit * profit_score +
            weight_sharpe * sharpe_score +
            weight_winrate * winrate
        )
        
        # Штраф за просадку
        fitness -= penalty_drawdown * drawdown_penalty
        
        # Штраф за малое количество сделок
        if result.total_trades < 10:
            fitness *= 0.5
        
        return fitness
    
    def optimize_grid_search(
        self,
        param_ranges: Optional[Dict[str, List[Any]]] = None,
        max_iterations: int = 1000,
    ) -> OptimizedParams:
        """
        Оптимизация параметров методом сетки (grid search)
        
        Args:
            param_ranges: диапазоны параметров для перебора
            max_iterations: максимальное количество итераций
        
        Returns:
            оптимальные параметры
        """
        if param_ranges is None:
            # Дефолтные диапазоны
            param_ranges = {
                'kama_len': [14, 21, 28, 35],
                'kama_fast': [2, 3, 4],
                'kama_slow': [15, 20, 25, 30],
                'entry_mult': [1.2, 1.4, 1.6, 1.8, 2.0],
                'exit_mult': [0.6, 0.8, 1.0, 1.2],
                'atr_len': [10, 14, 20],
                'atr_sl_mult': [1.8, 2.0, 2.2, 2.5],
                'atr_phase_len': [30, 50, 70],
                'atr_phase_mult': [0.8, 1.0, 1.2],
                'body_atr_mult': [0.3, 0.5, 0.7],
            }
        
        best_params = None
        best_fitness = float('-inf')
        best_result = None
        iteration = 0
        
        # Генерируем комбинации параметров
        from itertools import product
        
        # Ограничиваем количество комбинаций
        keys = list(param_ranges.keys())
        values = [param_ranges[k] for k in keys]
        
        total_combinations = np.prod([len(v) for v in values])
        if total_combinations > max_iterations:
            # Случайная выборка
            combinations = []
            for _ in range(max_iterations):
                combo = {}
                for k in keys:
                    combo[k] = np.random.choice(param_ranges[k])
                combinations.append(combo)
        else:
            combinations = [dict(zip(keys, combo)) for combo in product(*values)]
        
        print(f"Оптимизация: {len(combinations)} комбинаций параметров")
        
        for i, params in enumerate(combinations):
            if i >= max_iterations:
                break
            
            try:
                strategy = KAMAStrategy(**params)
                result = backtest_strategy(
                    candles=self.train_candles,
                    strategy=strategy,
                    initial_balance=self.initial_balance,
                    commission=self.commission,
                )
                
                fitness = self.calculate_fitness(result)
                
                if fitness > best_fitness:
                    best_fitness = fitness
                    best_params = params.copy()
                    best_result = result
                    
                    if (i + 1) % 10 == 0:
                        print(f"Итерация {i+1}/{len(combinations)}: фитнес={fitness:.4f}, прибыль={result.total_profit_pct:.2f}%")
            
            except Exception as e:
                continue
        
        if best_params is None:
            raise ValueError("Не удалось найти оптимальные параметры")
        
        # Проверяем на валидационной выборке
        strategy = KAMAStrategy(**best_params)
        val_result = backtest_strategy(
            candles=self.val_candles,
            strategy=strategy,
            initial_balance=self.initial_balance,
            commission=self.commission,
        )
        
        winrate = (best_result.winning_trades / best_result.total_trades * 100) if best_result.total_trades > 0 else 0
        
        return OptimizedParams(
            kama_len=best_params['kama_len'],
            kama_fast=best_params['kama_fast'],
            kama_slow=best_params['kama_slow'],
            entry_mult=best_params['entry_mult'],
            exit_mult=best_params['exit_mult'],
            atr_len=best_params['atr_len'],
            atr_sl_mult=best_params['atr_sl_mult'],
            atr_phase_len=best_params['atr_phase_len'],
            atr_phase_mult=best_params['atr_phase_mult'],
            body_atr_mult=best_params['body_atr_mult'],
            fitness_score=best_fitness,
            total_profit_pct=best_result.total_profit_pct,
            sharpe_ratio=best_result.sharpe_ratio,
            max_drawdown_pct=best_result.max_drawdown_pct,
            win_rate=winrate,
            total_trades=best_result.total_trades,
        )
    
    def optimize_genetic(
        self,
        population_size: int = 50,
        generations: int = 20,
        mutation_rate: float = 0.1,
    ) -> OptimizedParams:
        """
        Оптимизация параметров генетическим алгоритмом
        """
        # Базовые диапазоны
        param_bounds = {
            'kama_len': (10, 50),
            'kama_fast': (2, 10),
            'kama_slow': (10, 40),
            'entry_mult': (0.5, 3.0),
            'exit_mult': (0.3, 2.0),
            'atr_len': (5, 30),
            'atr_sl_mult': (1.0, 4.0),
            'atr_phase_len': (20, 100),
            'atr_phase_mult': (0.5, 2.0),
            'body_atr_mult': (0.1, 1.0),
        }
        
        # Инициализация популяции
        population = []
        for _ in range(population_size):
            individual = {}
            for param, (min_val, max_val) in param_bounds.items():
                if param in ['kama_len', 'kama_fast', 'kama_slow', 'atr_len', 'atr_phase_len']:
                    individual[param] = int(np.random.uniform(min_val, max_val))
                else:
                    individual[param] = np.random.uniform(min_val, max_val)
            population.append(individual)
        
        best_individual = None
        best_fitness = float('-inf')
        best_result = None
        
        for generation in range(generations):
            # Оценка фитнеса
            fitness_scores = []
            results = []
            
            for individual in population:
                try:
                    strategy = KAMAStrategy(**individual)
                    result = backtest_strategy(
                        candles=self.train_candles,
                        strategy=strategy,
                        initial_balance=self.initial_balance,
                        commission=self.commission,
                    )
                    fitness = self.calculate_fitness(result)
                    fitness_scores.append(fitness)
                    results.append(result)
                    
                    if fitness > best_fitness:
                        best_fitness = fitness
                        best_individual = individual.copy()
                        best_result = result
                
                except Exception:
                    fitness_scores.append(float('-inf'))
                    results.append(None)
            
            if generation % 5 == 0:
                print(f"Поколение {generation}/{generations}: лучший фитнес={best_fitness:.4f}")
            
            # Селекция, кроссовер и мутация
            if generation < generations - 1:
                # Топ 50% для размножения
                sorted_indices = np.argsort(fitness_scores)[::-1]
                elite_size = population_size // 2
                elite = [population[i] for i in sorted_indices[:elite_size]]
                
                # Новая популяция = элита + потомки
                new_population = elite.copy()
                
                while len(new_population) < population_size:
                    # Кроссовер
                    parent1 = elite[np.random.randint(0, len(elite))]
                    parent2 = elite[np.random.randint(0, len(elite))]
                    
                    child = {}
                    for param in param_bounds.keys():
                        if np.random.random() < 0.5:
                            child[param] = parent1[param]
                        else:
                            child[param] = parent2[param]
                    
                    # Мутация
                    if np.random.random() < mutation_rate:
                        param_to_mutate = np.random.choice(list(param_bounds.keys()))
                        min_val, max_val = param_bounds[param_to_mutate]
                        if param_to_mutate in ['kama_len', 'kama_fast', 'kama_slow', 'atr_len', 'atr_phase_len']:
                            child[param_to_mutate] = int(np.random.uniform(min_val, max_val))
                        else:
                            child[param_to_mutate] = np.random.uniform(min_val, max_val)
                    
                    new_population.append(child)
                
                population = new_population
        
        if best_individual is None:
            raise ValueError("Не удалось найти оптимальные параметры")
        
        winrate = (best_result.winning_trades / best_result.total_trades * 100) if best_result.total_trades > 0 else 0
        
        return OptimizedParams(
            kama_len=int(best_individual['kama_len']),
            kama_fast=int(best_individual['kama_fast']),
            kama_slow=int(best_individual['kama_slow']),
            entry_mult=float(best_individual['entry_mult']),
            exit_mult=float(best_individual['exit_mult']),
            atr_len=int(best_individual['atr_len']),
            atr_sl_mult=float(best_individual['atr_sl_mult']),
            atr_phase_len=int(best_individual['atr_phase_len']),
            atr_phase_mult=float(best_individual['atr_phase_mult']),
            body_atr_mult=float(best_individual['body_atr_mult']),
            fitness_score=best_fitness,
            total_profit_pct=best_result.total_profit_pct,
            sharpe_ratio=best_result.sharpe_ratio,
            max_drawdown_pct=best_result.max_drawdown_pct,
            win_rate=winrate,
            total_trades=best_result.total_trades,
        )


class AdaptiveStrategy:
    """
    Адаптивная стратегия, которая периодически переобучается на новых данных
    """
    
    def __init__(
        self,
        figi: str,
        optimized_params: OptimizedParams,
        retrain_period_days: int = 30,  # Переобучение раз в 30 дней
        pinescript_code: Optional[str] = None,  # Код PineScript, если есть
        name: Optional[str] = None,  # Название стратегии
    ):
        self.figi = figi
        self.optimized_params = optimized_params
        self.retrain_period_days = retrain_period_days
        self.last_retrain_date = datetime.now()
        self.pinescript_code = pinescript_code  # Код PineScript
        self.name = name  # Название стратегии
        
        # Создаем стратегию с оптимальными параметрами
        self.strategy = KAMAStrategy(
            kama_len=optimized_params.kama_len,
            kama_fast=optimized_params.kama_fast,
            kama_slow=optimized_params.kama_slow,
            entry_mult=optimized_params.entry_mult,
            exit_mult=optimized_params.exit_mult,
            atr_len=optimized_params.atr_len,
            atr_sl_mult=optimized_params.atr_sl_mult,
            atr_phase_len=optimized_params.atr_phase_len,
            atr_phase_mult=optimized_params.atr_phase_mult,
            body_atr_mult=optimized_params.body_atr_mult,
        )
    
    def should_retrain(self) -> bool:
        """Проверка, нужно ли переобучать стратегию"""
        days_since_retrain = (datetime.now() - self.last_retrain_date).days
        return days_since_retrain >= self.retrain_period_days
    
    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь"""
        result = {
            'figi': self.figi,
            'params': asdict(self.optimized_params),
            'last_retrain_date': self.last_retrain_date.isoformat(),
            'retrain_period_days': self.retrain_period_days,
        }
        if self.pinescript_code:
            result['pinescript_code'] = self.pinescript_code
        if self.name:
            result['name'] = self.name
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AdaptiveStrategy':
        """Десериализация из словаря"""
        params = OptimizedParams(**data['params'])
        strategy = cls(
            figi=data['figi'],
            optimized_params=params,
            retrain_period_days=data.get('retrain_period_days', 30),
            pinescript_code=data.get('pinescript_code', None),
            name=data.get('name', None),
        )
        strategy.last_retrain_date = datetime.fromisoformat(data['last_retrain_date'])
        return strategy


def save_optimized_strategy(strategy: AdaptiveStrategy, filepath: str):
    """Сохранить оптимизированную стратегию в файл"""
    def convert_to_native_types(obj):
        """Конвертирует numpy типы в нативные Python типы"""
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, dict):
            return {key: convert_to_native_types(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [convert_to_native_types(item) for item in obj]
        elif isinstance(obj, (datetime, timedelta)):
            return obj.isoformat()
        elif hasattr(obj, '__dict__'):
            return convert_to_native_types(obj.__dict__)
        else:
            return obj
    
    strategy_dict = strategy.to_dict()
    converted_dict = convert_to_native_types(strategy_dict)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(converted_dict, f, indent=2, ensure_ascii=False)


def load_optimized_strategy(filepath: str) -> AdaptiveStrategy:
    """Загрузить оптимизированную стратегию из файла"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return AdaptiveStrategy.from_dict(data)


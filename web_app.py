#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Веб-интерфейс для визуализации данных Tinkoff Invest API (Sandbox)
Поддерживает Windows и автоматическую установку зависимостей
"""
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from dataclasses import asdict

# Добавляем текущую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, jsonify, request
from tinkoff.invest.sandbox.client import SandboxClient
from tinkoff.invest import CandleInterval, MoneyValue, OperationState, OrderDirection, OrderType, InstrumentIdType
from tinkoff.invest.schemas import GetTechAnalysisRequest, IndicatorType, IndicatorInterval, TypeOfPrice, Deviation, Smoothing
from tinkoff.invest.utils import quotation_to_decimal, money_to_decimal, decimal_to_quotation, now
from uuid import uuid4
import logging
from strategies_backtest import KAMAStrategy, backtest_strategy, Candle as BacktestCandle
from strategy_optimizer import StrategyOptimizer, AdaptiveStrategy, OptimizedParams, save_optimized_strategy, load_optimized_strategy
from auto_trader import start_auto_trader, stop_auto_trader, get_trader_status, get_trader_logs, get_trader_chart_data
import json
import os
import glob
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def convert_to_native_types(obj):
    """Конвертирует numpy типы и другие не-JSON-совместимые типы в нативные Python типы"""
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
    elif isinstance(obj, Decimal):
        return float(obj)
    else:
        return obj

app = Flask(__name__)

def get_client():
    """Создает клиент для работы с API"""
    token = os.environ.get("INVEST_TOKEN")
    if not token:
        raise ValueError("INVEST_TOKEN не установлен")
    return SandboxClient(token)

@app.route('/')
def index():
    """Главная страница"""
    return render_template('index.html')

@app.route('/api/accounts')
def get_accounts():
    """Получить список аккаунтов"""
    try:
        with get_client() as client:
            accounts_response = client.users.get_accounts()
            accounts = []
            for acc in accounts_response.accounts:
                accounts.append({
                    'id': acc.id,
                    'name': acc.name,
                    'type': str(acc.type),
                    'status': str(acc.status),
                })
            return jsonify({'success': True, 'accounts': accounts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/accounts/create', methods=['POST'])
def create_account():
    """Создать новый sandbox аккаунт"""
    try:
        data = request.get_json() or {}
        account_name = data.get('name', '')
        
        with get_client() as client:
            response = client.sandbox.open_sandbox_account(name=account_name)
            return jsonify({
                'success': True,
                'account_id': response.account_id,
                'message': 'Аккаунт успешно создан'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/portfolio/<account_id>')
def get_portfolio(account_id):
    """Получить портфель по account_id"""
    try:
        with get_client() as client:
            portfolio = client.operations.get_portfolio(account_id=account_id)
            
            # Конвертируем данные портфеля
            portfolio_data = {
                'account_id': portfolio.account_id,
                'total_amount_portfolio': float(money_to_decimal(portfolio.total_amount_portfolio)) if portfolio.total_amount_portfolio else 0,
                'expected_yield': float(quotation_to_decimal(portfolio.expected_yield)) if portfolio.expected_yield else 0,
                'daily_yield': float(money_to_decimal(portfolio.daily_yield)) if portfolio.daily_yield else 0,
                'positions': []
            }
            
            for pos in portfolio.positions:
                position_data = {
                    'figi': pos.figi,
                    'instrument_type': pos.instrument_type,
                    'quantity': float(quotation_to_decimal(pos.quantity)),
                    'average_position_price': float(quotation_to_decimal(pos.average_position_price)) if pos.average_position_price else 0,
                    'expected_yield': float(quotation_to_decimal(pos.expected_yield)) if pos.expected_yield else 0,
                }
                
                # Получаем информацию об инструменте
                try:
                    instrument = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=pos.figi)
                    if instrument:
                        position_data['ticker'] = instrument.instrument.ticker
                        position_data['name'] = instrument.instrument.name
                except Exception as e:
                    logger.warning(f"Could not get instrument info for {pos.figi}: {e}")
                    position_data['ticker'] = pos.figi[:10]
                    position_data['name'] = pos.figi
                
                portfolio_data['positions'].append(position_data)
            
            return jsonify({'success': True, 'portfolio': portfolio_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/candles')
def get_candles():
    """Получить свечи для графика"""
    try:
        figi = request.args.get('figi', 'BBG004730N88')  # По умолчанию SBER
        interval = request.args.get('interval', 'hour')
        days = int(request.args.get('days', 7))
        
        # Маппинг интервалов
        interval_map = {
            'minute': CandleInterval.CANDLE_INTERVAL_1_MIN,
            '5minute': CandleInterval.CANDLE_INTERVAL_5_MIN,
            '15minute': CandleInterval.CANDLE_INTERVAL_15_MIN,
            'hour': CandleInterval.CANDLE_INTERVAL_HOUR,
            'day': CandleInterval.CANDLE_INTERVAL_DAY,
        }
        
        candle_interval = interval_map.get(interval, CandleInterval.CANDLE_INTERVAL_HOUR)
        
        with get_client() as client:
            to_date = now()
            from_date = to_date - timedelta(days=days)
            
            candles_response = client.market_data.get_candles(
                instrument_id=figi,
                from_=from_date,
                to=to_date,
                interval=candle_interval
            )
            
            candles_data = []
            for candle in candles_response.candles:
                candles_data.append({
                    'time': candle.time.isoformat(),
                    'open': float(quotation_to_decimal(candle.open)),
                    'high': float(quotation_to_decimal(candle.high)),
                    'low': float(quotation_to_decimal(candle.low)),
                    'close': float(quotation_to_decimal(candle.close)),
                    'volume': candle.volume,
                })
            
                # Получаем название инструмента
                try:
                    instrument = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi)
                    instrument_name = instrument.instrument.name if instrument else figi
                    instrument_ticker = instrument.instrument.ticker if instrument else figi[:10]
                except Exception as e:
                    logger.warning(f"Could not get instrument info for {figi}: {e}")
                    instrument_name = figi
                    instrument_ticker = figi[:10]
            
            return jsonify({
                'success': True,
                'candles': candles_data,
                'instrument_name': instrument_name,
                'instrument_ticker': instrument_ticker,
                'figi': figi
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/instruments/search')
def search_instruments():
    """Поиск инструментов по тикеру"""
    try:
        query = request.args.get('q', '')
        if not query:
            return jsonify({'success': True, 'instruments': []})
        
        with get_client() as client:
            response = client.instruments.find_instrument(query=query)
            instruments = []
            for instr in response.instruments[:20]:  # Ограничиваем 20 результатами
                instruments.append({
                    'figi': instr.figi,
                    'ticker': instr.ticker,
                    'name': instr.name,
                    'type': str(instr.instrument_type),
                })
            return jsonify({'success': True, 'instruments': instruments})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/user/info')
def get_user_info():
    """Получить информацию о пользователе"""
    try:
        with get_client() as client:
            user_info = client.users.get_info()
            return jsonify({
                'success': True,
                'user': {
                    'premium_status': user_info.premium_status,
                    'qualified_for_work_with': list(user_info.qualified_for_work_with) if user_info.qualified_for_work_with else [],
                }
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/positions/<account_id>')
def get_positions(account_id):
    """Получить позиции по account_id"""
    try:
        with get_client() as client:
            positions = client.operations.get_positions(account_id=account_id)
            
            positions_data = {
                'account_id': positions.account_id,
                'money': [],
                'securities': [],
                'futures': [],
                'options': []
            }
            
            # Денежные позиции
            for money in positions.money:
                positions_data['money'].append({
                    'currency': money.currency,
                    'value': float(money_to_decimal(money))
                })
            
            # Ценные бумаги
            for sec in positions.securities:
                position_info = {
                    'figi': sec.figi,
                    'balance': float(quotation_to_decimal(sec.balance)),
                    'blocked': float(quotation_to_decimal(sec.blocked)) if sec.blocked else 0,
                    'instrument_type': str(sec.instrument_type),
                }
                # Получаем информацию об инструменте
                try:
                    instrument = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=sec.figi)
                    if instrument:
                        position_info['ticker'] = instrument.instrument.ticker
                        position_info['name'] = instrument.instrument.name
                except Exception as e:
                    logger.warning(f"Could not get instrument info for {sec.figi}: {e}")
                    position_info['ticker'] = sec.figi[:10]
                    position_info['name'] = sec.figi
                
                positions_data['securities'].append(position_info)
            
            return jsonify({'success': True, 'positions': positions_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/operations/<account_id>')
def get_operations(account_id):
    """Получить операции по account_id"""
    try:
        days = int(request.args.get('days', 30))
        figi = request.args.get('figi', '')
        
        with get_client() as client:
            to_date = now()
            from_date = to_date - timedelta(days=days)
            
            operations = client.operations.get_operations(
                account_id=account_id,
                from_=from_date,
                to=to_date,
                figi=figi if figi else '',
                state=OperationState.OPERATION_STATE_EXECUTED
            )
            
            operations_data = []
            for op in operations.operations:
                op_data = {
                    'id': op.id,
                    'parent_operation_id': op.parent_operation_id if op.parent_operation_id else '',
                    'currency': op.currency,
                    'payment': float(money_to_decimal(op.payment)) if op.payment else 0,
                    'price': float(money_to_decimal(op.price)) if op.price else 0,
                    'state': str(op.state),
                    'quantity': op.quantity,
                    'quantity_rest': op.quantity_rest,
                    'figi': op.figi if op.figi else '',
                    'instrument_type': str(op.instrument_type),
                    'date': op.date.isoformat() if op.date else '',
                    'operation_type': str(op.operation_type),
                    'trades': []
                }
                
                # Информация об инструменте
                if op.figi:
                    try:
                        instrument = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=op.figi)
                        if instrument:
                            op_data['ticker'] = instrument.instrument.ticker
                            op_data['name'] = instrument.instrument.name
                    except Exception as e:
                        logger.warning(f"Could not get instrument info for {op.figi}: {e}")
                        op_data['ticker'] = op.figi[:10]
                        op_data['name'] = op.figi
                
                operations_data.append(op_data)
            
            return jsonify({'success': True, 'operations': operations_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/orders/<account_id>')
def get_orders(account_id):
    """Получить заявки по account_id"""
    try:
        with get_client() as client:
            orders = client.orders.get_orders(account_id=account_id)
            
            orders_data = []
            for order in orders.orders:
                order_data = {
                    'order_id': order.order_id,
                    'figi': order.figi,
                    'direction': str(order.direction),
                    'order_type': str(order.order_type),
                    'quantity': order.quantity,
                    'initial_order_price': float(quotation_to_decimal(order.initial_order_price)) if order.initial_order_price else 0,
                    'executed_order_price': float(quotation_to_decimal(order.executed_order_price)) if order.executed_order_price else 0,
                    'total_order_amount': float(money_to_decimal(order.total_order_amount)) if order.total_order_amount else 0,
                    'initial_commission': float(money_to_decimal(order.initial_commission)) if order.initial_commission else 0,
                    'executed_commission': float(money_to_decimal(order.executed_commission)) if order.executed_commission else 0,
                    'aci_value': float(money_to_decimal(order.aci_value)) if order.aci_value else 0,
                    'instrument_type': str(order.instrument_type),
                    'order_date': order.order_date.isoformat() if order.order_date else '',
                    'execution_report_status': str(order.execution_report_status),
                    'lots_requested': order.lots_requested,
                    'lots_executed': order.lots_executed,
                }
                
                # Информация об инструменте
                try:
                    instrument = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=order.figi)
                    if instrument:
                        order_data['ticker'] = instrument.instrument.ticker
                        order_data['name'] = instrument.instrument.name
                except Exception as e:
                    logger.warning(f"Could not get instrument info for {order.figi}: {e}")
                    order_data['ticker'] = order.figi[:10]
                    order_data['name'] = order.figi
                
                orders_data.append(order_data)
            
            return jsonify({'success': True, 'orders': orders_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/account/pay_in', methods=['POST'])
def pay_in():
    """Пополнить sandbox счет"""
    try:
        data = request.get_json() or {}
        account_id = data.get('account_id')
        amount = float(data.get('amount', 0))
        currency = data.get('currency', 'rub')
        
        if not account_id or amount <= 0:
            return jsonify({'success': False, 'error': 'Неверные параметры'}), 400
        
        with get_client() as client:
            # Конвертируем сумму в MoneyValue
            quotation = decimal_to_quotation(Decimal(str(amount)))
            money_value = MoneyValue(
                currency=currency,
                units=quotation.units,
                nano=quotation.nano
            )
            
            response = client.sandbox.sandbox_pay_in(
                account_id=account_id,
                amount=money_value
            )
            
            balance = float(money_to_decimal(response.balance)) if response.balance else 0
            
            return jsonify({
                'success': True,
                'balance': balance,
                'currency': currency,
                'message': 'Счет успешно пополнен'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/instrument/<figi>')
def get_instrument_info(figi):
    """Получить информацию об инструменте по FIGI"""
    try:
        with get_client() as client:
            try:
                instrument = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi)
                if instrument:
                    instr = instrument.instrument
                    return jsonify({
                        'success': True,
                        'figi': instr.figi,
                        'ticker': instr.ticker,
                        'name': instr.name,
                        'lot': instr.lot,
                        'currency': instr.currency,
                        'min_price_increment': float(quotation_to_decimal(instr.min_price_increment)) if instr.min_price_increment else 0,
                        'buy_available_flag': instr.buy_available_flag,
                        'sell_available_flag': instr.sell_available_flag,
                        'api_trade_available_flag': instr.api_trade_available_flag
                    })
            except Exception as e:
                logger.error(f"Error getting instrument {figi}: {e}")
                # Попробуем через поиск
                try:
                    response = client.instruments.find_instrument(query=figi)
                    if response.instruments:
                        instr = response.instruments[0]
                        return jsonify({
                            'success': True,
                            'figi': instr.figi,
                            'ticker': instr.ticker,
                            'name': instr.name,
                            'lot': 1,  # По умолчанию
                            'currency': 'rub',
                            'min_price_increment': 0.01,
                            'buy_available_flag': True,
                            'sell_available_flag': True,
                            'api_trade_available_flag': True
                        })
                except:
                    pass
            
            return jsonify({'success': False, 'error': 'Инструмент не найден'}), 404
    except Exception as e:
        logger.error(f"Error in get_instrument_info: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/last_price/<figi>')
def get_last_price(figi):
    """Получить последнюю цену по FIGI"""
    try:
        with get_client() as client:
            response = client.market_data.get_last_prices(figi=[figi])
            if response.last_prices:
                price_data = response.last_prices[0]
                return jsonify({
                    'success': True,
                    'figi': price_data.figi,
                    'price': float(quotation_to_decimal(price_data.price)) if price_data.price else 0,
                    'time': price_data.time.isoformat() if price_data.time else ''
                })
            return jsonify({'success': False, 'error': 'Цена не найдена'}), 404
    except Exception as e:
        logger.error(f"Error in get_last_price: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/account/balance/<account_id>')
def get_account_balance(account_id):
    """Получить баланс и доступные средства"""
    try:
        with get_client() as client:
            positions = client.operations.get_positions(account_id=account_id)
            portfolio = client.operations.get_portfolio(account_id=account_id)
            
            balance_data = {
                'money': [],
                'total_amount_portfolio': float(money_to_decimal(portfolio.total_amount_portfolio)) if portfolio.total_amount_portfolio else 0,
                'total_amount_currencies': float(money_to_decimal(portfolio.total_amount_currencies)) if portfolio.total_amount_currencies else 0,
                'securities': {}
            }
            
            # Денежные позиции
            for money in positions.money:
                balance_data['money'].append({
                    'currency': money.currency,
                    'value': float(money_to_decimal(money))
                })
            
            # Позиции по бумагам
            for sec in positions.securities:
                balance_data['securities'][sec.figi] = {
                    'balance': float(quotation_to_decimal(sec.balance)),
                    'available': float(quotation_to_decimal(sec.balance)) - float(quotation_to_decimal(sec.blocked)) if sec.blocked else float(quotation_to_decimal(sec.balance)),
                    'blocked': float(quotation_to_decimal(sec.blocked)) if sec.blocked else 0
                }
            
            return jsonify({'success': True, 'balance': balance_data})
    except Exception as e:
        logger.error(f"Error in get_account_balance: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/order/create', methods=['POST'])
def create_order():
    """Создать ордер (покупка/продажа) с проверками"""
    try:
        data = request.get_json() or {}
        account_id = data.get('account_id')
        figi = data.get('figi') or data.get('instrument_id', '')
        quantity = int(data.get('quantity', 0))
        direction_str = data.get('direction', 'BUY')
        order_type_str = data.get('order_type', 'MARKET')
        price = data.get('price')  # Optional для лимитных ордеров
        
        if not account_id or not figi or quantity <= 0:
            return jsonify({'success': False, 'error': 'Неверные параметры: укажите account_id, figi и quantity > 0'}), 400
        
        # Конвертируем направление
        direction_map = {
            'BUY': OrderDirection.ORDER_DIRECTION_BUY,
            'SELL': OrderDirection.ORDER_DIRECTION_SELL
        }
        direction = direction_map.get(direction_str.upper(), OrderDirection.ORDER_DIRECTION_BUY)
        
        # Конвертируем тип ордера
        order_type_map = {
            'MARKET': OrderType.ORDER_TYPE_MARKET,
            'LIMIT': OrderType.ORDER_TYPE_LIMIT,
            'BESTPRICE': OrderType.ORDER_TYPE_BESTPRICE
        }
        order_type = order_type_map.get(order_type_str.upper(), OrderType.ORDER_TYPE_MARKET)
        
        with get_client() as client:
            # Получаем информацию об инструменте
            try:
                instrument = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi)
                if not instrument:
                    return jsonify({'success': False, 'error': 'Инструмент не найден'}), 404
                instr = instrument.instrument
                lot_size = instr.lot
                currency = instr.currency
                
                # Проверка доступности торговли
                if direction == OrderDirection.ORDER_DIRECTION_BUY and not instr.buy_available_flag:
                    return jsonify({'success': False, 'error': 'Покупка недоступна для этого инструмента'}), 400
                if direction == OrderDirection.ORDER_DIRECTION_SELL and not instr.sell_available_flag:
                    return jsonify({'success': False, 'error': 'Продажа недоступна для этого инструмента'}), 400
                if not instr.api_trade_available_flag:
                    return jsonify({'success': False, 'error': 'Торговля через API недоступна для этого инструмента'}), 400
            except Exception as e:
                logger.error(f"Error getting instrument info: {e}")
                lot_size = 1  # По умолчанию
                currency = 'rub'
            
            # Проверка кратности лоту
            if quantity % lot_size != 0:
                return jsonify({
                    'success': False, 
                    'error': f'Количество должно быть кратно лоту ({lot_size}). Укажите количество, кратное {lot_size}'
                }), 400
            
            # Получаем баланс и позиции
            positions = client.operations.get_positions(account_id=account_id)
            portfolio = client.operations.get_portfolio(account_id=account_id)
            
            # Проверки перед созданием ордера
            if direction == OrderDirection.ORDER_DIRECTION_BUY:
                # Проверка баланса для покупки
                available_money = 0
                for money in positions.money:
                    if money.currency.lower() == currency.lower():
                        available_money = float(money_to_decimal(money))
                        break
                
                # Получаем текущую цену для расчета стоимости
                try:
                    price_response = client.market_data.get_last_prices(figi=[figi])
                    if price_response.last_prices:
                        current_price = float(quotation_to_decimal(price_response.last_prices[0].price))
                        estimated_cost = current_price * quantity * lot_size
                        
                        if available_money < estimated_cost * 1.01:  # 1% запас на комиссию
                            return jsonify({
                                'success': False,
                                'error': f'Недостаточно средств. Доступно: {available_money:.2f} {currency.upper()}, требуется: ~{estimated_cost:.2f} {currency.upper()}'
                            }), 400
                except Exception as e:
                    logger.warning(f"Could not check price: {e}")
                    # Для лимитного ордера используем указанную цену
                    if order_type == OrderType.ORDER_TYPE_LIMIT and price:
                        estimated_cost = float(price) * quantity * lot_size
                        if available_money < estimated_cost * 1.01:
                            return jsonify({
                                'success': False,
                                'error': f'Недостаточно средств. Доступно: {available_money:.2f} {currency.upper()}, требуется: ~{estimated_cost:.2f} {currency.upper()}'
                            }), 400
                
            elif direction == OrderDirection.ORDER_DIRECTION_SELL:
                # Проверка доступного количества для продажи
                available_quantity = 0
                for sec in positions.securities:
                    if sec.figi == figi:
                        available_quantity = float(quotation_to_decimal(sec.balance))
                        blocked = float(quotation_to_decimal(sec.blocked)) if sec.blocked else 0
                        available_quantity = available_quantity - blocked
                        break
                
                required_quantity = quantity * lot_size
                if available_quantity < required_quantity:
                    return jsonify({
                        'success': False,
                        'error': f'Недостаточно бумаг для продажи. Доступно: {available_quantity:.0f}, требуется: {required_quantity:.0f} (лотов: {quantity})'
                    }), 400
            
            # Конвертируем цену если указана
            price_quotation = None
            if price and order_type == OrderType.ORDER_TYPE_LIMIT:
                price_quotation = decimal_to_quotation(Decimal(str(price)))
            
            response = client.orders.post_order(
                account_id=account_id,
                instrument_id=figi,
                quantity=quantity,
                direction=direction,
                order_type=order_type,
                order_id=str(uuid4()),
                price=price_quotation
            )
            
            return jsonify({
                'success': True,
                'order_id': response.order_id,
                'execution_report_status': str(response.execution_report_status),
                'message': 'Ордер создан успешно'
            })
    except Exception as e:
        logger.error(f"Error in create_order: {e}", exc_info=True)
        error_msg = str(e)
        # Улучшенная обработка ошибок
        if 'insufficient' in error_msg.lower() or 'недостаточно' in error_msg.lower():
            return jsonify({'success': False, 'error': f'Недостаточно средств или бумаг: {error_msg}'}), 400
        elif 'invalid' in error_msg.lower() or 'неверный' in error_msg.lower():
            return jsonify({'success': False, 'error': f'Неверные параметры: {error_msg}'}), 400
        else:
            return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/order/cancel', methods=['POST'])
def cancel_order():
    """Отменить ордер"""
    try:
        data = request.get_json() or {}
        account_id = data.get('account_id')
        order_id = data.get('order_id')
        
        if not account_id or not order_id:
            return jsonify({'success': False, 'error': 'Неверные параметры'}), 400
        
        with get_client() as client:
            response = client.orders.cancel_order(
                account_id=account_id,
                order_id=order_id
            )
            
            return jsonify({
                'success': True,
                'time': response.time.isoformat() if response.time else '',
                'message': 'Ордер отменен'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/indicators/<figi>')
def get_indicators(figi):
    """Получить технические индикаторы"""
    try:
        indicator_type_str = request.args.get('type', 'RSI')
        days = int(request.args.get('days', 7))
        
        # Маппинг типов индикаторов
        indicator_map = {
            'RSI': IndicatorType.INDICATOR_TYPE_RSI,
            'MACD': IndicatorType.INDICATOR_TYPE_MACD,
            'SMA': IndicatorType.INDICATOR_TYPE_SMA,
            'EMA': IndicatorType.INDICATOR_TYPE_EMA,
            'BB': IndicatorType.INDICATOR_TYPE_BB,
        }
        indicator_type = indicator_map.get(indicator_type_str.upper(), IndicatorType.INDICATOR_TYPE_RSI)
        
        with get_client() as client:
            to_date = now()
            from_date = to_date - timedelta(days=days)
            
            # Получаем instrument_uid по figi
            try:
                instrument = client.instruments.get_instrument_by(id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi)
                instrument_uid = instrument.instrument.uid if instrument else figi
            except Exception as e:
                logger.warning(f"Could not get instrument uid for {figi}: {e}")
                instrument_uid = figi
            
            request_obj = GetTechAnalysisRequest(
                indicator_type=indicator_type,
                instrument_uid=instrument_uid,
                from_=from_date,
                to=to_date,
                interval=IndicatorInterval.INDICATOR_INTERVAL_HOUR,
                type_of_price=TypeOfPrice.TYPE_OF_PRICE_CLOSE,
                length=14,  # Период для RSI и других индикаторов
                smoothing=Smoothing(fast_length=12, slow_length=26, signal_smoothing=9) if indicator_type == IndicatorType.INDICATOR_TYPE_MACD else None,
                deviation=Deviation(deviation_multiplier=decimal_to_quotation(Decimal('2.0'))) if indicator_type == IndicatorType.INDICATOR_TYPE_BB else None
            )
            
            response = client.market_data.get_tech_analysis(request=request_obj)
            
            indicators_data = []
            for indicator in response.technical_indicators:
                ind_data = {
                    'time': indicator.timestamp.isoformat() if indicator.timestamp else '',
                }
                if indicator.signal:
                    ind_data['signal'] = float(quotation_to_decimal(indicator.signal))
                if indicator.macd:
                    ind_data['macd'] = float(quotation_to_decimal(indicator.macd))
                if indicator.middle_band:
                    ind_data['middle_band'] = float(quotation_to_decimal(indicator.middle_band))
                if indicator.upper_band:
                    ind_data['upper_band'] = float(quotation_to_decimal(indicator.upper_band))
                if indicator.lower_band:
                    ind_data['lower_band'] = float(quotation_to_decimal(indicator.lower_band))
                indicators_data.append(ind_data)
            
            return jsonify({
                'success': True,
                'indicators': indicators_data,
                'indicator_type': indicator_type_str
            })
    except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/backtest', methods=['POST'])
def backtest_strategy_api():
    """Бэктест стратегии на исторических данных"""
    try:
        data = request.get_json() or {}
        figi = data.get('figi')
        strategy_type = data.get('strategy_type', 'KAMA')
        days = int(data.get('days', 30))
        interval_str = data.get('interval', 'hour')
        
        # Параметры стратегии KAMA
        strategy_params = data.get('strategy_params', {})
        
        # Начальный баланс
        initial_balance = float(data.get('initial_balance', 100000.0))
        commission = float(data.get('commission', 0.05))
        
        if not figi:
            return jsonify({'success': False, 'error': 'Не указан FIGI'}), 400
        
        # Маппинг интервалов
        interval_map = {
            'minute': CandleInterval.CANDLE_INTERVAL_1_MIN,
            '5minute': CandleInterval.CANDLE_INTERVAL_5_MIN,
            '15minute': CandleInterval.CANDLE_INTERVAL_15_MIN,
            'hour': CandleInterval.CANDLE_INTERVAL_HOUR,
            'day': CandleInterval.CANDLE_INTERVAL_DAY,
        }
        candle_interval = interval_map.get(interval_str, CandleInterval.CANDLE_INTERVAL_HOUR)
        
        with get_client() as client:
            # Загружаем исторические данные
            to_date = now()
            from_date = to_date - timedelta(days=days)
            
            candles_response = client.market_data.get_candles(
                instrument_id=figi,
                from_=from_date,
                to=to_date,
                interval=candle_interval
            )
            
            if not candles_response.candles:
                return jsonify({'success': False, 'error': 'Нет данных для тестирования'}), 404
            
            # Конвертируем в формат для бэктестинга
            backtest_candles = []
            for candle in candles_response.candles:
                backtest_candles.append(BacktestCandle(
                    time=candle.time,
                    open=float(quotation_to_decimal(candle.open)),
                    high=float(quotation_to_decimal(candle.high)),
                    low=float(quotation_to_decimal(candle.low)),
                    close=float(quotation_to_decimal(candle.close)),
                    volume=candle.volume,
                ))
            
            # Создаем стратегию
            if strategy_type == 'KAMA':
                strategy = KAMAStrategy(
                    kama_len=int(strategy_params.get('kama_len', 21)),
                    kama_fast=int(strategy_params.get('kama_fast', 2)),
                    kama_slow=int(strategy_params.get('kama_slow', 20)),
                    entry_mult=float(strategy_params.get('entry_mult', 1.6)),
                    exit_mult=float(strategy_params.get('exit_mult', 0.8)),
                    atr_len=int(strategy_params.get('atr_len', 14)),
                    atr_sl_mult=float(strategy_params.get('atr_sl_mult', 2.2)),
                    atr_phase_len=int(strategy_params.get('atr_phase_len', 50)),
                    atr_phase_mult=float(strategy_params.get('atr_phase_mult', 1.0)),
                    body_atr_mult=float(strategy_params.get('body_atr_mult', 0.5)),
                )
            else:
                return jsonify({'success': False, 'error': f'Неизвестный тип стратегии: {strategy_type}'}), 400
            
            # Запускаем бэктест
            result = backtest_strategy(
                candles=backtest_candles,
                strategy=strategy,
                initial_balance=initial_balance,
                commission=commission,
            )
            
            # Форматируем результат
            trades_data = []
            for trade in result.trades:
                trades_data.append({
                    'entry_time': trade.entry_time.isoformat() if trade.entry_time else None,
                    'exit_time': trade.exit_time.isoformat() if trade.exit_time else None,
                    'entry_price': trade.entry_price,
                    'exit_price': trade.exit_price,
                    'quantity': trade.quantity,
                    'direction': trade.direction,
                    'profit': trade.profit,
                    'profit_pct': trade.profit_pct,
                })
            
            result_data = {
                'success': True,
                'result': {
                    'total_trades': int(result.total_trades),
                    'winning_trades': int(result.winning_trades),
                    'losing_trades': int(result.losing_trades),
                    'total_profit': float(result.total_profit),
                    'total_profit_pct': float(result.total_profit_pct),
                    'max_drawdown': float(result.max_drawdown),
                    'max_drawdown_pct': float(result.max_drawdown_pct),
                    'sharpe_ratio': float(result.sharpe_ratio),
                    'final_balance': float(result.final_balance),
                    'trades': trades_data,
                    'equity_curve': [float(x) for x in result.equity_curve],
                }
            }
            return jsonify(convert_to_native_types(result_data))
    
    except Exception as e:
        logger.error(f"Error in backtest_strategy_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/optimize', methods=['POST'])
def optimize_strategy_api():
    """Оптимизация стратегии на исторических данных (4-5 лет)"""
    try:
        data = request.get_json() or {}
        figi = data.get('figi')
        optimization_method = data.get('method', 'genetic')  # 'grid' or 'genetic'
        years = int(data.get('years', 4))  # Количество лет истории
        interval_str = data.get('interval', 'day')  # Используем дневные свечи для длинной истории
        
        if not figi:
            return jsonify({'success': False, 'error': 'Не указан FIGI'}), 400
        
        # Маппинг интервалов
        interval_map = {
            'minute': CandleInterval.CANDLE_INTERVAL_1_MIN,
            '5minute': CandleInterval.CANDLE_INTERVAL_5_MIN,
            '15minute': CandleInterval.CANDLE_INTERVAL_15_MIN,
            'hour': CandleInterval.CANDLE_INTERVAL_HOUR,
            'day': CandleInterval.CANDLE_INTERVAL_DAY,
        }
        candle_interval = interval_map.get(interval_str, CandleInterval.CANDLE_INTERVAL_DAY)
        
        # Начальный баланс
        initial_balance = float(data.get('initial_balance', 100000.0))
        commission = float(data.get('commission', 0.05))
        
        with get_client() as client:
            # Загружаем длинную историю (4-5 лет)
            to_date = now()
            from_date = to_date - timedelta(days=years * 365)
            
            logger.info(f"Загрузка исторических данных с {from_date} по {to_date} для {figi}")
            
            # Загружаем свечи порциями (API может ограничивать)
            all_candles = []
            current_from = from_date
            
            while current_from < to_date:
                current_to = min(current_from + timedelta(days=365), to_date)
                
                candles_response = client.market_data.get_candles(
                    instrument_id=figi,
                    from_=current_from,
                    to=current_to,
                    interval=candle_interval
                )
                
                if candles_response.candles:
                    all_candles.extend(candles_response.candles)
                
                current_from = current_to
                
                if len(all_candles) > 0:
                    logger.info(f"Загружено {len(all_candles)} свечей...")
            
            if len(all_candles) < 100:
                return jsonify({'success': False, 'error': f'Недостаточно данных для оптимизации: {len(all_candles)} свечей'}), 400
            
            # Конвертируем в формат для бэктестинга
            backtest_candles = []
            for candle in all_candles:
                backtest_candles.append(BacktestCandle(
                    time=candle.time,
                    open=float(quotation_to_decimal(candle.open)),
                    high=float(quotation_to_decimal(candle.high)),
                    low=float(quotation_to_decimal(candle.low)),
                    close=float(quotation_to_decimal(candle.close)),
                    volume=candle.volume,
                ))
            
            logger.info(f"Начало оптимизации на {len(backtest_candles)} свечах методом {optimization_method}")
            
            # Создаем оптимизатор
            optimizer = StrategyOptimizer(
                candles=backtest_candles,
                initial_balance=initial_balance,
                commission=commission,
            )
            
            # Запускаем оптимизацию
            if optimization_method == 'genetic':
                optimized_params = optimizer.optimize_genetic(
                    population_size=int(data.get('population_size', 50)),
                    generations=int(data.get('generations', 20)),
                )
            else:  # grid
                optimized_params = optimizer.optimize_grid_search(
                    max_iterations=int(data.get('max_iterations', 500)),
                )
            
            # Создаем адаптивную стратегию
            adaptive_strategy = AdaptiveStrategy(
                figi=figi,
                optimized_params=optimized_params,
            )
            
            # Сохраняем стратегию с уникальным именем (дата и время обучения)
            import os
            os.makedirs('strategies', exist_ok=True)
            
            # Создаем уникальное имя файла с датой и временем
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            strategy_file = f"strategies/{figi}_optimized_{timestamp}.json"
            
            # Также сохраняем как последнюю версию для обратной совместимости
            latest_file = f"strategies/{figi}_optimized.json"
            save_optimized_strategy(adaptive_strategy, strategy_file)
            save_optimized_strategy(adaptive_strategy, latest_file)
            
            logger.info(f"Стратегия сохранена: {strategy_file} и {latest_file}")
            
            # Тестируем на валидационной выборке
            strategy = KAMAStrategy(
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
            
            val_result = backtest_strategy(
                candles=optimizer.val_candles,
                strategy=strategy,
                initial_balance=initial_balance,
                commission=commission,
            )
            
            # Конвертируем все значения в нативные Python типы
            result_data = {
                'success': True,
                'optimized_params': {
                    'kama_len': int(optimized_params.kama_len),
                    'kama_fast': int(optimized_params.kama_fast),
                    'kama_slow': int(optimized_params.kama_slow),
                    'entry_mult': float(optimized_params.entry_mult),
                    'exit_mult': float(optimized_params.exit_mult),
                    'atr_len': int(optimized_params.atr_len),
                    'atr_sl_mult': float(optimized_params.atr_sl_mult),
                    'atr_phase_len': int(optimized_params.atr_phase_len),
                    'atr_phase_mult': float(optimized_params.atr_phase_mult),
                    'body_atr_mult': float(optimized_params.body_atr_mult),
                },
                'metrics': {
                    'fitness_score': float(optimized_params.fitness_score),
                    'train_profit_pct': float(optimized_params.total_profit_pct),
                    'train_sharpe_ratio': float(optimized_params.sharpe_ratio),
                    'train_max_drawdown_pct': float(optimized_params.max_drawdown_pct),
                    'train_win_rate': float(optimized_params.win_rate),
                    'train_total_trades': int(optimized_params.total_trades),
                    'val_profit_pct': float(val_result.total_profit_pct),
                    'val_sharpe_ratio': float(val_result.sharpe_ratio),
                    'val_max_drawdown_pct': float(val_result.max_drawdown_pct),
                    'val_win_rate': float((val_result.winning_trades / val_result.total_trades * 100) if val_result.total_trades > 0 else 0),
                    'val_total_trades': int(val_result.total_trades),
                },
                'strategy_file': strategy_file,
            }
            
            return jsonify(convert_to_native_types(result_data))
    
    except Exception as e:
        logger.error(f"Error in optimize_strategy_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/list')
def list_strategies_api():
    """Получить список всех сохраненных стратегий"""
    try:
        strategies_dir = "strategies"
        if not os.path.exists(strategies_dir):
            return jsonify({'success': True, 'strategies': []})
        
        # Ищем все файлы стратегий (включая версии с timestamp)
        strategy_files = glob.glob(f"{strategies_dir}/*_optimized*.json")
        strategies = []
        
        for file_path in strategy_files:
            try:
                strategy = load_optimized_strategy(file_path)
                file_name = os.path.basename(file_path)
                
                # Извлекаем timestamp из имени файла, если есть
                timestamp_str = None
                if '_optimized_' in file_name and file_name.endswith('.json'):
                    # Формат: {figi}_optimized_{timestamp}.json
                    parts = file_name.replace('_optimized_', '|').replace('.json', '').split('|')
                    if len(parts) == 2:
                        timestamp_str = parts[1]
                
                strategies.append({
                    'figi': strategy.figi,
                    'file': file_name,
                    'timestamp': timestamp_str,
                    'last_retrain_date': strategy.last_retrain_date.isoformat(),
                    'should_retrain': strategy.should_retrain(),
                    'fitness_score': strategy.optimized_params.fitness_score,
                    'total_profit_pct': strategy.optimized_params.total_profit_pct,
                    'win_rate': strategy.optimized_params.win_rate,
                    'has_pinescript': bool(strategy.pinescript_code) if hasattr(strategy, 'pinescript_code') else False,
                    'name': strategy.name if hasattr(strategy, 'name') and strategy.name else None,
                })
            except Exception as e:
                logger.warning(f"Не удалось загрузить стратегию из {file_path}: {e}")
                continue
        
        # Сортируем стратегии: сначала по FIGI, потом по дате обучения (новые первыми)
        strategies.sort(key=lambda x: (x['figi'], x['last_retrain_date']), reverse=True)
        
        # Конвертируем все значения в нативные типы
        strategies_converted = [convert_to_native_types(s) for s in strategies]
        return jsonify({'success': True, 'strategies': strategies_converted})
    except Exception as e:
        logger.error(f"Error in list_strategies_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/delete', methods=['POST'])
def delete_strategy_api():
    """Удалить стратегию по имени файла"""
    try:
        data = request.get_json() or {}
        file_name = data.get('file')
        
        if not file_name:
            return jsonify({'success': False, 'error': 'Не указано имя файла'}), 400
        
        # Безопасность: проверяем, что файл находится в директории strategies
        if '..' in file_name or '/' in file_name.replace('\\', '/') or file_name.startswith('/'):
            return jsonify({'success': False, 'error': 'Недопустимое имя файла'}), 400
        
        file_path = os.path.join('strategies', file_name)
        
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'error': 'Файл не найден'}), 404
        
        # Удаляем файл
        os.remove(file_path)
        logger.info(f"Стратегия удалена: {file_path}")
        
        return jsonify({
            'success': True,
            'message': f'Стратегия {file_name} успешно удалена'
        })
    
    except Exception as e:
        logger.error(f"Error in delete_strategy_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/load/<figi>')
def load_strategy_api(figi):
    """Загрузить сохраненную оптимизированную стратегию"""
    try:
        strategy_file = f"strategies/{figi}_optimized.json"
        
        if not os.path.exists(strategy_file):
            return jsonify({'success': False, 'error': 'Стратегия не найдена'}), 404
        
        strategy = load_optimized_strategy(strategy_file)
        
        params_dict = asdict(strategy.optimized_params)
        result_data = {
            'success': True,
            'figi': strategy.figi,
            'params': convert_to_native_types(params_dict),
            'last_retrain_date': strategy.last_retrain_date.isoformat(),
            'should_retrain': strategy.should_retrain(),
        }
        return jsonify(result_data)
    except Exception as e:
        logger.error(f"Error in load_strategy_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/auto_trade/start', methods=['POST'])
def start_auto_trade_api():
    """Запустить автоматическую торговлю"""
    try:
        data = request.get_json() or {}
        figi = data.get('figi')
        account_id = data.get('account_id')
        interval_str = data.get('interval', 'hour')
        trade_percent = float(data.get('trade_percent', 100.0))
        
        if not figi or not account_id:
            return jsonify({'success': False, 'error': 'Не указан FIGI или account_id'}), 400
        
        strategy_file = f"strategies/{figi}_optimized.json"
        if not os.path.exists(strategy_file):
            return jsonify({'success': False, 'error': 'Стратегия не найдена. Обучите стратегию сначала.'}), 404
        
        token = os.environ.get("INVEST_TOKEN")
        if not token:
            return jsonify({'success': False, 'error': 'INVEST_TOKEN не установлен'}), 500
        
        interval_map = {
            'minute': CandleInterval.CANDLE_INTERVAL_1_MIN,
            '5minute': CandleInterval.CANDLE_INTERVAL_5_MIN,
            '15minute': CandleInterval.CANDLE_INTERVAL_15_MIN,
            'hour': CandleInterval.CANDLE_INTERVAL_HOUR,
            'day': CandleInterval.CANDLE_INTERVAL_DAY,
        }
        interval = interval_map.get(interval_str, CandleInterval.CANDLE_INTERVAL_HOUR)
        
        trader = start_auto_trader(
            figi=figi,
            account_id=account_id,
            strategy_file=strategy_file,
            token=token,
            interval=interval,
            trade_percent=trade_percent,
        )
        
        return jsonify({
            'success': True,
            'message': 'Автоматическая торговля запущена',
            'status': trader.get_status(),
        })
    
    except Exception as e:
        logger.error(f"Error in start_auto_trade_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/auto_trade/stop', methods=['POST'])
def stop_auto_trade_api():
    """Остановить автоматическую торговлю"""
    try:
        data = request.get_json() or {}
        figi = data.get('figi')
        account_id = data.get('account_id')
        
        if not figi or not account_id:
            return jsonify({'success': False, 'error': 'Не указан FIGI или account_id'}), 400
        
        stopped = stop_auto_trader(figi=figi, account_id=account_id)
        
        if stopped:
            return jsonify({
                'success': True,
                'message': 'Автоматическая торговля остановлена',
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Автоматическая торговля не найдена',
            }), 404
    
    except Exception as e:
        logger.error(f"Error in stop_auto_trade_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/auto_trade/status')
def auto_trade_status_api():
    """Получить статус автоматической торговли"""
    try:
        figi = request.args.get('figi')
        account_id = request.args.get('account_id')
        
        if not figi or not account_id:
            return jsonify({'success': False, 'error': 'Не указан FIGI или account_id'}), 400
        
        status = get_trader_status(figi=figi, account_id=account_id)
        
        if status:
            return jsonify({
                'success': True,
                'status': status,
            })
        else:
            return jsonify({
                'success': True,
                'status': {'is_running': False},
            })
    
    except Exception as e:
        logger.error(f"Error in auto_trade_status_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/auto_trade/logs')
def auto_trade_logs_api():
    """Получить логи автоматической торговли"""
    try:
        figi = request.args.get('figi')
        account_id = request.args.get('account_id')
        limit = int(request.args.get('limit', 100))
        
        if not figi or not account_id:
            return jsonify({'success': False, 'error': 'Не указан FIGI или account_id'}), 400
        
        logs = get_trader_logs(figi=figi, account_id=account_id, limit=limit)
        
        if logs is not None:
            return jsonify({
                'success': True,
                'logs': logs,
            })
        else:
            return jsonify({
                'success': True,
                'logs': [],
            })
    
    except Exception as e:
        logger.error(f"Error in auto_trade_logs_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/auto_trade/chart_data')
def auto_trade_chart_data_api():
    """Получить данные для графика автоторговли"""
    try:
        figi = request.args.get('figi')
        account_id = request.args.get('account_id')
        
        if not figi or not account_id:
            return jsonify({'success': False, 'error': 'Не указан FIGI или account_id'}), 400
        
        chart_data = get_trader_chart_data(figi=figi, account_id=account_id)
        
        if chart_data is not None:
            # Конвертируем данные для JSON
            converted_data = convert_to_native_types(chart_data)
            return jsonify({
                'success': True,
                'chart_data': converted_data,
            })
        else:
            return jsonify({
                'success': True,
                'chart_data': {
                    'price_history': [],
                    'signals_history': [],
                    'equity_history': [],
                    'current_position': 0,
                    'last_signal': None,
                    'candles_count': 0,
                },
            })
    
    except Exception as e:
        logger.error(f"Error in auto_trade_chart_data_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/save_from_code', methods=['POST'])
def save_strategy_from_code_api():
    """Сохранить стратегию из кода (Python или PineScript)"""
    try:
        data = request.get_json() or {}
        code = data.get('code', '')
        pinescript_code = data.get('pinescript_code', '')
        figi = data.get('figi', '')
        strategy_name = data.get('name', '')
        code_type = data.get('code_type', 'python')  # 'python' или 'pinescript'
        
        if not figi:
            return jsonify({'success': False, 'error': 'Не указан FIGI'}), 400
        
        # Если это PineScript, сохраняем его отдельно
        logger.info(f"Получен запрос на сохранение стратегии: code_type={code_type}, has_code={bool(code)}, has_pinescript_code={bool(pinescript_code)}")
        
        if code_type == 'pinescript' or pinescript_code:
            pinescript_code = pinescript_code or code
            
            if not pinescript_code:
                logger.error("Не указан код PineScript")
                return jsonify({'success': False, 'error': 'Не указан код PineScript'}), 400
            
            logger.info(f"Сохранение PineScript стратегии для FIGI: {figi}, длина кода: {len(pinescript_code)}")
            
            # Создаем базовую стратегию с дефолтными параметрами
            # Для использования PineScript стратегии её нужно будет обучить
            default_params = OptimizedParams(
                kama_len=21,
                kama_fast=2,
                kama_slow=20,
                entry_mult=1.6,
                exit_mult=0.8,
                atr_len=14,
                atr_sl_mult=2.2,
                atr_phase_len=50,
                atr_phase_mult=1.0,
                body_atr_mult=0.5,
                fitness_score=0.0,
                total_profit_pct=0.0,
                sharpe_ratio=0.0,
                max_drawdown_pct=0.0,
                win_rate=0.0,
                total_trades=0
            )
            
            strategy = AdaptiveStrategy(
                figi=figi,
                optimized_params=default_params,
                pinescript_code=pinescript_code,
                name=strategy_name if strategy_name else None,
            )
            
            # Сохраняем стратегию с уникальным именем
            os.makedirs('strategies', exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            strategy_file = f"strategies/{figi}_optimized_{timestamp}.json"
            latest_file = f"strategies/{figi}_optimized.json"
            
            try:
                save_optimized_strategy(strategy, strategy_file)
                save_optimized_strategy(strategy, latest_file)
                logger.info(f"Стратегия из PineScript успешно сохранена: {strategy_file} и {latest_file}")
                logger.info(f"PineScript код сохранен, длина: {len(pinescript_code)} символов")
            except Exception as save_error:
                logger.error(f"Ошибка при сохранении PineScript стратегии: {save_error}", exc_info=True)
                return jsonify({'success': False, 'error': f'Ошибка при сохранении файла: {str(save_error)}'}), 500
            
            return jsonify({
                'success': True,
                'message': 'Стратегия из PineScript успешно сохранена. Для использования необходимо обучить стратегию на исторических данных.',
                'file': strategy_file,
            })
        
        # Обычный Python код
        if not code:
            return jsonify({'success': False, 'error': 'Не указан код стратегии'}), 400
        
        # Пытаемся выполнить код и получить стратегию
        try:
            # Создаем безопасное окружение для выполнения кода
            safe_globals = {
                'OptimizedParams': OptimizedParams,
                'AdaptiveStrategy': AdaptiveStrategy,
                '__builtins__': __builtins__,
            }
            
            # Выполняем код
            exec(code, safe_globals)
            
            # Ищем переменную strategy в результате выполнения
            if 'strategy' not in safe_globals:
                return jsonify({'success': False, 'error': 'В коде должна быть определена переменная strategy'}), 400
            
            strategy = safe_globals['strategy']
            
            if not isinstance(strategy, AdaptiveStrategy):
                return jsonify({'success': False, 'error': 'Переменная strategy должна быть экземпляром AdaptiveStrategy'}), 400
            
            # Используем FIGI из стратегии, если он указан, иначе используем переданный FIGI
            strategy_figi = strategy.figi if strategy.figi else figi
            if figi and strategy_figi != figi:
                # Если передан FIGI и он отличается от FIGI в стратегии, обновляем его
                strategy.figi = figi
                strategy_figi = figi
                logger.info(f"FIGI обновлен с {strategy.figi} на {figi}")
            
            # Сохраняем PineScript код если он был передан
            if pinescript_code:
                strategy.pinescript_code = pinescript_code
            
            # Сохраняем стратегию с уникальным именем (дата и время)
            os.makedirs('strategies', exist_ok=True)
            
            # Создаем уникальное имя файла с датой и временем
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            strategy_file = f"strategies/{strategy_figi}_optimized_{timestamp}.json"
            
            # Также сохраняем как последнюю версию для обратной совместимости
            latest_file = f"strategies/{strategy_figi}_optimized.json"
            
            try:
                save_optimized_strategy(strategy, strategy_file)
                save_optimized_strategy(strategy, latest_file)
                logger.info(f"Стратегия из кода сохранена: {strategy_file} и {latest_file}")
            except Exception as save_error:
                logger.error(f"Ошибка при сохранении стратегии: {save_error}", exc_info=True)
                return jsonify({'success': False, 'error': f'Ошибка при сохранении файла: {str(save_error)}'}), 500
            
            return jsonify({
                'success': True,
                'message': 'Стратегия успешно сохранена',
                'file': strategy_file,
            })
        
        except SyntaxError as e:
            return jsonify({'success': False, 'error': f'Синтаксическая ошибка в коде: {e}'}), 400
        except Exception as e:
            return jsonify({'success': False, 'error': f'Ошибка выполнения кода: {e}'}), 400
    
    except Exception as e:
        logger.error(f"Error in save_strategy_from_code_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/detail/<figi>')
def strategy_detail_api(figi):
    """Получить детальную информацию о стратегии"""
    try:
        strategy_file = f"strategies/{figi}_optimized.json"
        
        if not os.path.exists(strategy_file):
            return jsonify({'success': False, 'error': 'Стратегия не найдена'}), 404
        
        strategy = load_optimized_strategy(strategy_file)
        
        # Читаем файл для получения полного JSON
        with open(strategy_file, 'r', encoding='utf-8') as f:
            strategy_json = json.load(f)
        
        result = {
            'success': True,
            'strategy': convert_to_native_types(strategy_json),
            'params': convert_to_native_types(asdict(strategy.optimized_params)),
            'figi': strategy.figi,
            'last_retrain_date': strategy.last_retrain_date.isoformat(),
            'should_retrain': strategy.should_retrain(),
        }
        
        # Добавляем PineScript код если есть
        if hasattr(strategy, 'pinescript_code') and strategy.pinescript_code:
            result['pinescript_code'] = strategy.pinescript_code
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Error in strategy_detail_api: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    import platform
    
    # Проверяем наличие токена
    if not os.environ.get("INVEST_TOKEN"):
        print("❌ Ошибка: переменная окружения INVEST_TOKEN не установлена")
        if platform.system() == 'Windows':
            print("Установите токен командой:")
            print("  set INVEST_TOKEN=ваш_sandbox_токен")
            print("\nИли через настройки системы:")
            print("  Панель управления → Система → Переменные среды")
        else:
            print("Установите токен командой:")
            print("  export INVEST_TOKEN='ваш_sandbox_токен'")
        sys.exit(1)
    
    print("🚀 Запуск веб-сервера...")
    print("📊 Откройте в браузере: http://localhost:8080")
    print("Для остановки нажмите Ctrl+C")
    print()
    app.run(debug=True, host='0.0.0.0', port=8080)


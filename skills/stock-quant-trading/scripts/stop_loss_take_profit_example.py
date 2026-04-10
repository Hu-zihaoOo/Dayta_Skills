#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
止损止盈自动卖出脚本模板
适用于：币安/OKX（加密货币）、东方财富/同花顺（A股）等

⚠️ 使用前请务必阅读以下注意事项：
1. 本脚本仅供学习研究，实盘使用请充分测试
2. API Key 请勿泄露，建议使用只读+交易权限的子账号
3. 建议先在模拟盘（Paper Trading）验证至少3个月
4. 股市有风险，止损不止损都有可能亏损
"""

import time
import logging
from datetime import datetime

# ============================================================
# 方式一：ccxt（适用于币安/OKX 等加密货币交易所）
# 安装：pip install ccxt
# ============================================================
# import ccxt
#
# def run_ccxt_strategy():
#     exchange = ccxt.binance({
#         'apiKey': 'YOUR_API_KEY',
#         'secret': 'YOUR_SECRET',
#         'enableRateLimit': True,
#     })
#
#     SYMBOL = "BTC/USDT"
#     STOP_LOSS_RATIO = 0.02    # 止损：跌2%卖出
#     TAKE_PROFIT_RATIO = 0.03  # 止盈：涨3%卖出
#     CHECK_INTERVAL = 5        # 轮询间隔（秒）
#
#     # 获取持仓
#     positions = exchange.fetch_balance()['info']['positions']
#     position = next(
#         (p for p in positions if p['symbol'] == 'BTCUSDT' and float(p['positionAmt']) != 0),
#         None
#     )
#
#     if not position:
#         print("无持仓，退出")
#         return
#
#     entry_price = float(position['entryPrice'])
#     qty = abs(float(position['positionAmt']))
#     print(f"持仓 BTC，数量: {qty}，均价: {entry_price}")
#
#     stop_loss_price = entry_price * (1 - STOP_LOSS_RATIO)
#     take_profit_price = entry_price * (1 + TAKE_PROFIT_RATIO)
#     print(f"止损价: {stop_loss_price}, 止盈价: {take_profit_price}")
#
#     while True:
#         ticker = exchange.fetch_ticker(SYMBOL)
#         current_price = ticker['last']
#         print(f"[{datetime.now().strftime('%H:%M:%S')}] 当前价格: {current_price}")
#
#         if current_price <= stop_loss_price:
#             exchange.create_market_sell_order(SYMBOL, qty)
#             print(f"触发止损！价格: {current_price}")
#             break
#         elif current_price >= take_profit_price:
#             exchange.create_market_sell_order(SYMBOL, qty)
#             print(f"触发止盈！价格: {current_price}")
#             break
#
#         time.sleep(CHECK_INTERVAL)


# ============================================================
# 方式二：A股 - 聚宽平台（免费数据+模拟盘）
# 文档：https://www.joinquant.com/help#helpName=api
# ============================================================
# import jqdata
#
# def run_jq_strategy():
#     # 获取持仓
#     positions = get_positions()
#     for security, position in positions.items():
#         cost = position.init_cash * position.weight  # 实际请按真实持仓计算
#         current_price = get_current_price(security)
#
#         stop_loss_price = cost * 0.98   # 止损：-2%
#         take_profit_price = cost * 1.03 # 止盈：+3%
#
#         if current_price <= stop_loss_price:
#             order_target(security, 0)  # 全卖
#             log.info(f"触发止损: {security} @ {current_price}")
#         elif current_price >= take_profit_price:
#             order_target(security, 0)
#             log.info(f"触发止盈: {security} @ {current_price}")


# ============================================================
# 方式三：A股 - EasyTrader（同花顺客户端自动化）
# 安装：pip install easytrader
# ⚠️ 需要同花顺客户端运行，不适合服务器部署
# ============================================================
# import easytrader
#
# def run_easytrader_strategy():
#     trader = easytrader.use('tonghuashun')
#     trader.connect()
#
#     while True:
#         positions = trader.get_stock_list()
#         for stock_code in positions:
#             balance = trader.get_stock_balance(stock_code)
#             current_price = trader.get_stock_price(stock_code)
#
#             stop_loss = balance['avg_cost'] * 0.98
#             take_profit = balance['avg_cost'] * 1.03
#
#             if current_price <= stop_loss:
#                 trader.sell(stock_code, current_price, balance['current_amount'])
#             elif current_price >= take_profit:
#                 trader.sell(stock_code, current_price, balance['current_amount'])
#
#         time.sleep(60)  # 每分钟检查一次


if __name__ == "__main__":
    print("=" * 50)
    print("止损止盈自动卖出脚本")
    print("请根据你的市场选择对应的函数")
    print("=" * 50)
    # run_ccxt_strategy()  # 加密货币
    # run_jq_strategy()     # A股（聚宽）
    # run_easytrader_strategy()  # A股（同花顺）

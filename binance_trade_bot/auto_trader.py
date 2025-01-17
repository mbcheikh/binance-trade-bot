import time
from datetime import datetime
from typing import Dict, List

from sqlalchemy.orm import Session

from .binance_api_manager import AllTickers, BinanceAPIManager
from .config import Config
from .database import Database
from .logger import Logger
from .models import Coin, CoinValue, Pair


class AutoTrader:
    def __init__(self, binance_manager: BinanceAPIManager, database: Database, logger: Logger, config: Config):
        self.manager = binance_manager
        self.db = database
        self.logger = logger
        self.config = config

    def initialize(self):
        self.initialize_trade_thresholds()

    def transaction_through_bridge(self, pair: Pair, all_tickers: AllTickers):
        """
        Jump from the source coin to the destination coin through bridge coin
        """
        btc_price=all_tickers.get_price("BTCUSDT")
        if btc_price< self.config.STOPLOSS:
            return None
        balance = self.manager.get_currency_balance(pair.from_coin.symbol)

        if not (balance):
            self.logger.info(
                f"Incorrect coin balance {pair.from_coin}"
            )
            return None

        direct_pair_price=all_tickers.get_price(pair.from_coin_id + pair.to_coin_id)
        inverse_pair_price=all_tickers.get_price(pair.to_coin_id + pair.from_coin_id)
        if direct_pair_price and float(direct_pair_price)>1e-6:
            self.logger.info(
                "Direct pair {0}{1} exists. Selling {0} for {1}".format(pair.from_coin_id, pair.to_coin_id)
            )
            result = self.manager.sell_alt(pair.from_coin, pair.to_coin, all_tickers)
            if result:
                """
                # big bug
                # we will update db with the price 
                if pair.to_coin.symbol==self.config.BRIDGE_SYMBOL:
                    price=float(result['price'])
                else:
                    price=float(result['price'])*all_tickers.get_price(pair.to_coin+self.config.BRIDGE)
                """
                price=all_tickers.get_price(pair.to_coin+self.config.BRIDGE)
        elif inverse_pair_price and float(inverse_pair_price)>1e-06:
            self.logger.info(
                "Direct pair {0}{1} exists. Buying {0} with {1}".format(pair.to_coin_id, pair.from_coin_id)
            )
            result = self.manager.buy_alt(pair.to_coin, pair.from_coin, all_tickers, False)
            if result:
                if pair.from_coin.symbol==self.config.BRIDGE_SYMBOL:
                    price=float(result['price'])
                else:
                    price = float(result['price']) * all_tickers.get_price(pair.from_coin + self.config.BRIDGE)
        else:
            if self.manager.sell_alt(pair.from_coin, self.config.BRIDGE, all_tickers) is None:
                self.logger.info("Couldn't sell, going back to scouting mode...")
                return None

            result = self.manager.buy_alt(pair.to_coin, self.config.BRIDGE, all_tickers, False)
            if result:
                price=float(result['price'])

        if result is not None:
            self.update_trade_threshold(pair.to_coin, price, all_tickers)
            return result
        self.logger.info("Couldn't buy, going back to scouting mode...")
        return None

    def update_trade_threshold(self, coin: Coin, coin_price: float, all_tickers: AllTickers):
        """
        Update all the coins with the threshold of buying the current held coin
        """

        self.db.set_coins(self.config.SUPPORTED_COIN_LIST)
        if coin_price is None:
            self.logger.info("Skipping update... current coin {} not found".format(coin + self.config.BRIDGE))
            return

        session: Session
        with self.db.db_session() as session:
            for pair in session.query(Pair).filter(Pair.to_coin == coin):
                if pair.from_coin.symbol==self.config.BRIDGE_SYMBOL:
                    from_coin_price=1
                else:
                    from_coin_price = all_tickers.get_price(pair.from_coin + self.config.BRIDGE)

                if from_coin_price is None:
                    self.logger.info(
                        "Skipping update for coin {} not found".format(pair.from_coin + self.config.BRIDGE)
                    )
                    continue
                anc_ratio=pair.ratio
                pair.ratio = from_coin_price / coin_price
                self.logger.info(
                    "Update "+pair.from_coin.symbol + pair.to_coin.symbol +" Anc:"+str(anc_ratio)+" ratio:"+str(pair.ratio)+" From price:"+str(from_coin_price)+" To "+ str( coin_price)
                )

    def initialize_trade_thresholds(self):
        """
        Initialize the buying threshold of all the coins for trading between them
        """
        all_tickers = self.manager.get_all_market_tickers()

        session: Session
        with self.db.db_session() as session:
            for pair in session.query(Pair).filter(Pair.ratio.is_(None)).all():
                if not pair.from_coin.enabled or not pair.to_coin.enabled:
                    continue
                self.logger.info(f"Initializing {pair.from_coin} vs {pair.to_coin}")

                if pair.from_coin.symbol==self.config.BRIDGE_SYMBOL:
                    from_coin_price=1
                else:
                    from_coin_price = all_tickers.get_price(pair.from_coin + self.config.BRIDGE)
                if from_coin_price is None:
                    self.logger.info(
                        "Skipping initializing {}, symbol not found".format(pair.from_coin + self.config.BRIDGE)
                    )
                    continue
                if pair.to_coin.symbol==self.config.BRIDGE_SYMBOL:
                    to_coin_price=1
                else:
                    to_coin_price = all_tickers.get_price(pair.to_coin + self.config.BRIDGE)
                if to_coin_price is None:
                    self.logger.info(
                        "Skipping initializing {}, symbol not found".format(pair.to_coin + self.config.BRIDGE)
                    )
                    continue

                pair.ratio = from_coin_price / to_coin_price

    def scout(self):
        """
        Scout for potential jumps from the current coin to another coin
        """
        raise NotImplementedError()

    def _get_ratios(self, coin: Coin, coin_price_bridge: float, all_tickers: AllTickers):
        """
        Given a coin, get the current price ratio for every other enabled coin
        """
        ratio_dict: Dict[Pair, float] = {}
        current_balances=self.manager.get_balances()
        current_balances_dict={d['asset']:float(d['free']) for d in current_balances if float(d['free'])>0}


        for pair in self.db.get_pairs_from(coin):
            min_amount=self.config.MIN_AMOUNT

            if pair.to_coin.symbol==self.config.BRIDGE_SYMBOL:
                optional_coin_price=1
            else:
                optional_coin_price=all_tickers.get_price(pair.to_coin.symbol+self.config.BRIDGE_SYMBOL)

            if pair.to_coin.symbol in current_balances_dict:
                min_to_ignore=self.config.MIN_AMOUNT
                if pair.to_coin.symbol=='BNB':
                    min_to_ignore+=self.config.MIN_BNB
                if current_balances_dict[pair.to_coin.symbol]*optional_coin_price > min_to_ignore:
                    continue
            pair_exists = (all_tickers.get_price(pair.from_coin + pair.to_coin),
                           all_tickers.get_price(pair.to_coin + pair.from_coin))
            if pair_exists[0] and pair_exists[0]>1e-06:
                coin_price = pair_exists[0]
                optional_coin_price = 1
                transaction_fee = self.manager.get_fee(pair.from_coin, pair.to_coin, True)
            elif pair_exists[1] and pair_exists[1]>1e-06:
                coin_price = 1
                optional_coin_price = pair_exists[1]
                transaction_fee = self.manager.get_fee(pair.to_coin, pair.from_coin, False)
            else:
                if self.config.ONLY_DIRECT_PAIRS:
                    continue
                coin_price = coin_price_bridge
                optional_coin_price = all_tickers.get_price(pair.to_coin + self.config.BRIDGE)
                transaction_fee = self.manager.get_fee(pair.from_coin, self.config.BRIDGE, True) + self.manager.get_fee(
                    pair.to_coin, self.config.BRIDGE, False
                )

            if optional_coin_price is None:
                self.logger.info(
                    "Skipping scouting... optional coin {} not found".format(pair.to_coin + self.config.BRIDGE)
                )
                continue

            self.db.log_scout(pair, pair.ratio, coin_price, optional_coin_price)

            # Obtain (current coin)/(optional coin)
            coin_opt_coin_ratio = coin_price / optional_coin_price

            ratio_dict[pair] = (
                                       coin_opt_coin_ratio - transaction_fee * self.config.SCOUT_MULTIPLIER * coin_opt_coin_ratio
                               ) - pair.ratio
        return ratio_dict

    def _jump_to_best_coin(self, coin: Coin, coin_price: float, all_tickers: AllTickers):
        """
        Given a coin, search for a coin to jump to
        """
        ratio_dict = self._get_ratios(coin, coin_price, all_tickers)
        best_ratio=max(ratio_dict,key=ratio_dict.get)
        # keep only ratios bigger than zero
        self.logger.info(f"BEST: {best_ratio}  {ratio_dict[best_ratio]}" )
        ratio_dict = {k: v for k, v in ratio_dict.items() if v > 0}

        # if we have any viable options, pick the one with the biggest ratio
        if ratio_dict:
            best_pair = max(ratio_dict, key=ratio_dict.get)
            self.logger.info(f"Will be jumping from {coin} to {best_pair.to_coin_id}")

            self.transaction_through_bridge(best_pair, all_tickers)
        return ratio_dict

    def bridge_scout(self):
        """
        If we have any bridge coin leftover, buy a coin with it that we won't immediately trade out of
        """
        bridge_balance = self.manager.get_currency_balance(self.config.BRIDGE.symbol)
        all_tickers = self.manager.get_all_market_tickers()

        for coin in self.db.get_coins():
            current_coin_price = all_tickers.get_price(coin + self.config.BRIDGE)

            if current_coin_price is None:
                continue

            ratio_dict = self._get_ratios(coin, current_coin_price, all_tickers)
            if not any(v > 0 for v in ratio_dict.values()):
                # There will only be one coin where all the ratios are negative. When we find it, buy it if we can
                if bridge_balance > self.manager.get_min_notional(coin.symbol, self.config.BRIDGE.symbol):
                    self.logger.info(f"Will be purchasing {coin} using bridge coin")
                    self.manager.buy_alt(coin, self.config.BRIDGE, all_tickers)
                    return coin
        return None

    def update_values(self):
        """
        Log current value state of all altcoi n balances against BTC and USDT in DB.
        """
        print("Logging values...")
        gottickers=False
        while not gottickers:
            try:
                all_ticker_values = self.manager.get_all_market_tickers()
                gottickers=True
            except:
                time.sleep(5)

        now = datetime.now()

        session: Session
        bridge_symbol=self.config.BRIDGE_SYMBOL
        with self.db.db_session() as session:
            coins: List[Coin] = session.query(Coin).all()
            balances=self.manager.get_balances()
            balances_dict={d['asset']:float(d['free'])+float(d['locked']) for d in balances if float(d['free'])+float(d['locked'])>0}
            total_balance_usd=total_balance_btc=0
            for coin in coins:
                btc_value=usd_value=0
                if coin.symbol not in balances_dict:
                    continue
                balance = balances_dict[coin.symbol]
                if  coin.symbol=='USDT':
                    usd_value=1
                    btc_value = 1 / all_ticker_values.get_price('BTCUSDT')
                elif coin.symbol=='BTC':
                    btc_value=1
                    usd_value=all_ticker_values.get_price('BTCUSDT')
                else:
                    usd_value = all_ticker_values.get_price(coin + 'USDT')
                    btc_value = all_ticker_values.get_price(coin + 'BTC')

                btc_price=all_ticker_values.get_price('BTCUSDT')
                if usd_value and btc_value:
                    self.logger.info(
                        f"coin: {coin.symbol} price: USDT {usd_value} BTC {btc_value} Balance: USDT {usd_value * balance} BTC {btc_value * balance}"
                    )
                    total_balance_btc+=btc_value * balance
                    total_balance_usd+=usd_value * balance
                if coin.symbol!=self.config.BRIDGE_SYMBOL:
                    cv = CoinValue(coin, balance, usd_value, btc_value, datetime=now)
                    session.add(cv)
                #self.db.send_update(cv)
            self.logger.info(f"Total balance USDT: {total_balance_usd} BTC: {total_balance_btc} BTC price: {btc_price}" )

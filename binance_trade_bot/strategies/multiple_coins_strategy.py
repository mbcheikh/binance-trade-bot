from binance_trade_bot.auto_trader import AutoTrader


class Strategy(AutoTrader):
    def scout(self):
        """
        Scout for potential jumps from the current coin to another coin
        """
        all_tickers = self.manager.get_all_market_tickers()
        have_coin = False

        # last coin bought
        current_balances=self.manager.get_balances()
        current_balances_dict={d['asset']:float(d['free']) for d in current_balances if float(d['free'])>0}
        for coin in self.db.get_coins():
            if not coin.symbol in current_balances_dict:
                continue
            current_coin_balance = current_balances_dict[coin.symbol]
            coin_price = all_tickers.get_price(coin + self.config.BRIDGE)

            if coin_price is None:
                self.logger.info("Skipping scouting... current coin {} not found".format(coin + self.config.BRIDGE))
                continue

            min_notional = self.manager.get_min_notional(coin.symbol, self.config.BRIDGE.symbol)

            if  coin_price * current_coin_balance < min_notional:
                continue

            have_coin = True

            # Display on the console, the current coin+Bridge, so users can see *some* activity and not think the bot
            # has stopped. Not logging though to reduce log size.
            print(f"Scouting for best trades. Current coin: {coin} ")

            self._jump_to_best_coin(coin, coin_price, all_tickers)

        if not have_coin:
            self.bridge_scout()

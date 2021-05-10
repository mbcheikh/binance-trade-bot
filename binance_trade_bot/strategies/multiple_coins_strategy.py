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

        list_coins=self.db.get_coins()
        for coin in list_coins:
            if not coin.symbol in current_balances_dict:
                continue
            current_coin_balance = current_balances_dict[coin.symbol]
            if coin.symbol==self.config.BRIDGE_SYMBOL:
                coin_price=1
                min_notional=20
            else:
                try:
                    coin_price = all_tickers.get_price(coin + self.config.BRIDGE)
                    min_notional = self.manager.get_min_notional(coin.symbol, self.config.BRIDGE.symbol)+10
                except:
                    self.logger.info("Skipping scouting... current coin {} not found".format(coin + self.config.BRIDGE))
                    continue


            if coin.symbol =='BNB':
                if coin_price * current_coin_balance <= self.config.MIN_BNB+min_notional:
                    continue
            else:
                if  coin_price * current_coin_balance < min_notional:
                    continue


            have_coin = True

            # Display on the console, the current coin+Bridge, so users can see *some* activity and not think the bot
            # has stopped. Not logging though to reduce log size.
            print(f"Scouting for best trades. Current coin: {coin} ")

            result=self._jump_to_best_coin(coin, coin_price, all_tickers)
            if result:
                #refresh prices and balances
                all_tickers = self.manager.get_all_market_tickers()
                current_balances = self.manager.get_balances()
                current_balances_dict = {d['asset']: float(d['free']) for d in current_balances if float(d['free']) > 0}
                self.db.set_coins(self.config.SUPPORTED_COIN_LIST)

        if not have_coin:
            self.bridge_scout()

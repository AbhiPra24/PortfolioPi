from breeze_connect import BreezeConnect
from config import settings

class SessionExpiredError(Exception):
    pass

class BreezeClient:
    def __init__(self, session_token: str):
        self.breeze = BreezeConnect(api_key=settings.api_key.get_secret_value())
        try:
            self.breeze.generate_session(api_secret=settings.api_secret.get_secret_value(), session_token=session_token)
        except Exception as e:
            raise SessionExpiredError(f"Session generation failed: {e}")

    # ONLY ALLOWED METHODS:
    def get_customer_details(self, *args, **kwargs):
        return self.breeze.get_customer_details(*args, **kwargs)
        
    def get_demat_holdings(self, *args, **kwargs):
        return self.breeze.get_demat_holdings(*args, **kwargs)
        
    def get_funds(self, *args, **kwargs):
        return self.breeze.get_funds(*args, **kwargs)
        
    def get_historical_data(self, *args, **kwargs):
        return self.breeze.get_historical_data(*args, **kwargs)
        
    def get_historical_data_v2(self, *args, **kwargs):
        return self.breeze.get_historical_data_v2(*args, **kwargs)
        
    def get_portfolio_holding(self, *args, **kwargs):
        return self.breeze.get_portfolio_holding(*args, **kwargs)
        
    def get_portfolio_position(self, *args, **kwargs):
        return self.breeze.get_portfolio_position(*args, **kwargs)
        
    def get_quotes(self, *args, **kwargs):
        return self.breeze.get_quotes(*args, **kwargs)
        
    def get_names(self, *args, **kwargs):
        return self.breeze.get_names(*args, **kwargs)
        
    # DO NOT EXPOSE ANY ORDER PLACEMENT METHODS

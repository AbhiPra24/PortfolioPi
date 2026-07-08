from telegram.ext import Application, CommandHandler
from config import settings
from .handlers import portfolio_command, signals_command, refresh_session_command, help_command

def create_app() -> Application:
    app = Application.builder().token(settings.telegram_bot_token.get_secret_value()).build()
    
    app.add_handler(CommandHandler("portfolio", portfolio_command))
    app.add_handler(CommandHandler("signals", signals_command))
    app.add_handler(CommandHandler("refresh_session", refresh_session_command))
    app.add_handler(CommandHandler("help", help_command))
    
    return app

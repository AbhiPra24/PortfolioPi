import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse

from bot.app import create_app
from core.database import init_db
from core.scheduler import start_scheduler
from core.session_store import save_session

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class CallbackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.handle_redirect()
    def do_GET(self):
        self.handle_redirect()
        
    def handle_redirect(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        token = qs.get("apisession", [None])[0]
        
        if token:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(save_session(token))
            loop.close()
            logging.info(f"Successfully captured Breeze token from ICICI redirect.")
            
        self.send_response(302)
        self.send_header('Location', f'http://192.168.1.28:8653/?apisession={token}' if token else 'http://192.168.1.28:8653/')
        self.end_headers()

def run_callback_server():
    logging.info("Starting ICICI callback receiver on port 8654...")
    server = HTTPServer(('0.0.0.0', 8654), CallbackHandler)
    server.serve_forever()

async def main():
    await init_db()
    app = create_app()
    start_scheduler(app)
    await app.initialize()
    
    from bot.app import COMMANDS
    await app.bot.set_my_commands(COMMANDS)
    
    await app.start()
    await app.updater.start_polling()

    threading.Thread(target=run_callback_server, daemon=True).start()

    stop_signal = asyncio.Event()
    await stop_signal.wait()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import os
import logging
import threading
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, ChannelPrivateError, UsernameNotOccupiedError, FloodWaitError
from datetime import datetime, timedelta

# --- Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# IMPORTANT: Replace with your own credentials
api_id = 28757544
api_hash = '9d570a40813302f7bafb05fa0cd0ee4c'
phone_number = '+13464021220'

# --- Flask App Setup ---
app = Flask(__name__)
CORS(app)

# --- Global State ---
scraper_lock = threading.Lock()
last_request_time = datetime.now()
rate_limit_seconds = 10 # 10-second cool-down per request

class TelegramMessageScraper:
    def __init__(self):
        self.client = None
    
    async def connect_client(self):
        """Connect and authorize the Telegram client"""
        try:
            self.client = TelegramClient('message_session', api_id, api_hash)
            await self.client.start(phone=phone_number)
            
            if not await self.client.is_user_authorized():
                logger.error("Authorization failed.")
                return False
            
            logger.info("Telegram client authorized successfully!")
            return True
            
        except SessionPasswordNeededError:
            logger.error("Two-step verification is enabled.")
            return False
        except Exception as e:
            logger.error(f"Client connection error: {str(e)}")
            return False

    async def scrape_usernames_from_messages(self, target_group, message_limit=5000, time_limit_hours=0):
        """Scrape usernames from Telegram group message history with time filter"""
        if not await self.connect_client():
            return {
                'success': False,
                'error': 'Failed to authorize Telegram client.'
            }

        try:
            logger.info(f"Fetching message history from {target_group}...")
            
            entity = await self.client.get_entity(target_group)
            
            usernames = {}
            messages_scanned = 0
            
            # Calculate the time limit
            time_limit = datetime.now() - timedelta(hours=time_limit_hours) if time_limit_hours > 0 else None
            
            async for message in self.client.iter_messages(entity, limit=message_limit):
                messages_scanned += 1
                
                # Check against the time limit if it exists
                if time_limit and message.date.replace(tzinfo=None) < time_limit:
                    logger.info(f"Reached time limit of {time_limit_hours} hours. Stopping scan.")
                    break
                
                if not message.sender:
                    continue
                
                sender = message.sender
                
                if hasattr(sender, 'username') and sender.username:
                    username_key = sender.username.lower()
                    if username_key not in usernames:
                        usernames[username_key] = {
                            'username': sender.username,
                            'first_name': getattr(sender, 'first_name', '') or '',
                            'last_name': getattr(sender, 'last_name', '') or '',
                            'id': sender.id,
                            'message_count': 1,
                            'last_message_date': message.date.strftime('%Y-%m-%d %H:%M:%S') if message.date else 'Unknown'
                        }
                    else:
                        usernames[username_key]['message_count'] += 1
                        if message.date:
                            usernames[username_key]['last_message_date'] = message.date.strftime('%Y-%m-%d %H:%M:%S')
                
                if messages_scanned % 100 == 0:
                    logger.info(f"Scanned {messages_scanned} messages, found {len(usernames)} unique usernames")
            
            username_list = sorted(list(usernames.values()), key=lambda x: x['message_count'], reverse=True)
            
            logger.info(f"Successfully scraped {len(username_list)} usernames from {messages_scanned} messages in '{entity.title}'")
            
            return {
                'success': True,
                'group_title': entity.title,
                'usernames': username_list,
                'total_count': len(username_list),
                'messages_scanned': messages_scanned
            }
        
        except (ChannelPrivateError, UsernameNotOccupiedError):
            return {
                'success': False,
                'error': 'The group or channel is private or does not exist. Please ensure you are a member.'
            }
        except FloodWaitError as e:
            return {
                'success': False,
                'error': f'Telegram is rate-limiting your requests. Please wait {e.seconds} seconds.'
            }
        except Exception as e:
            logger.error(f"Error scraping usernames: {str(e)}")
            return {
                'success': False,
                'error': f'Server error: {str(e)}',
                'usernames': [],
                'total_count': 0,
                'messages_scanned': 0
            }
        finally:
            if self.client:
                await self.client.disconnect()

# Initialize a global scraper object, but do not connect the client yet
scraper = TelegramMessageScraper()

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram History Scraper</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .container { background: white; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.1); padding: 40px; width: 100%; max-width: 700px; }
        h1 { text-align: center; color: #1e3a8a; margin-bottom: 8px; }
        .subtitle { text-align: center; color: #4b5563; margin-bottom: 30px; }
        .info-box { background: #e0e7ff; border-left: 4px solid #4f46e5; padding: 15px; margin-bottom: 20px; border-radius: 4px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: 600; color: #1f2937; }
        input[type="text"], input[type="number"] { width: 100%; padding: 12px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 16px; }
        .scrape-btn { width: 100%; padding: 15px; background-color: #4f46e5; color: white; border: none; border-radius: 8px; font-size: 18px; font-weight: 600; cursor: pointer; transition: background-color 0.2s; }
        .scrape-btn:hover { background-color: #4338ca; }
        .scrape-btn:disabled { background-color: #9ca3af; cursor: not-allowed; }
        .results { margin-top: 30px; padding: 20px; background: #f9fafb; border-radius: 8px; display: none; }
        .results.show { display: block; }
        .stats { display: flex; justify-content: space-around; background: white; padding: 15px; border-radius: 8px; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .stat-item { text-align: center; }
        .stat-item strong { font-size: 1.2rem; display: block; margin-bottom: 4px; }
        .username-list { max-height: 400px; overflow-y: auto; background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .username-item { padding: 12px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
        .username-item:last-child { border-bottom: none; }
        .username-info { flex-grow: 1; }
        .message-count { background-color: #e0e7ff; color: #4f46e5; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }
        .error { color: #dc2626; background: #fee2e2; padding: 15px; border-radius: 8px; margin-top: 15px; }
        .loading { text-align: center; padding: 20px; }
        .spinner { border: 4px solid #e5e7eb; border-top: 4px solid #4f46e5; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 15px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <h1>Telegram Scraper</h1>
        <p class="subtitle">Extract usernames from a group's message history</p>
        
        <div class="info-box">
            <p><strong>Note:</strong> This tool only works for groups you are already a member of.</p>
        </div>

        <form id="scrapeForm">
            <div class="form-group">
                <label for="groupLink">Group Link or Username:</label>
                <input type="text" id="groupLink" name="groupLink" placeholder="@groupname or https://t.me/joinchat/..." required>
            </div>
            <div class="form-group">
                <label for="timeLimit">Look Back (hours):</label>
                <input type="number" id="timeLimit" name="timeLimit" value="0" min="0" max="720" placeholder="0 for all messages">
                <small style="color: #6b7280;">(Optional) Scrape messages from the last N hours.</small>
            </div>
            
            <button type="submit" class="scrape-btn" id="scrapeBtn">
                Scrape Messages
            </button>
        </form>
        
        <div id="results" class="results">
            <div id="loading" class="loading" style="display: none;">
                <div class="spinner"></div>
                <p>Scanning message history... This may take a while for large groups.</p>
            </div>
            
            <div id="success" style="display: none;">
                <h3 id="groupTitle"></h3>
                <div class="stats">
                    <div class="stat-item">
                        <p><strong>📊 Messages Scanned</strong></p>
                        <span id="messagesScanned">0</span>
                    </div>
                    <div class="stat-item">
                        <p><strong>👥 Unique Users</strong></p>
                        <span id="totalCount">0</span>
                    </div>
                </div>
                <div id="usernameList" class="username-list"></div>
            </div>
            
            <div id="error" class="error" style="display: none;"></div>
        </div>
    </div>

    <script>
        document.getElementById('scrapeForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const groupLink = document.getElementById('groupLink').value.trim();
            const timeLimitHours = parseInt(document.getElementById('timeLimit').value.trim(), 10);
            const scrapeBtn = document.getElementById('scrapeBtn');
            const results = document.getElementById('results');
            const loading = document.getElementById('loading');
            const success = document.getElementById('success');
            const error = document.getElementById('error');
            
            // Reset UI
            results.classList.add('show');
            loading.style.display = 'block';
            success.style.display = 'none';
            error.style.display = 'none';
            scrapeBtn.disabled = true;
            scrapeBtn.textContent = 'Scanning Messages...';
            
            try {
                const response = await fetch('/scrape', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        group_link: groupLink,
                        time_limit_hours: timeLimitHours
                    })
                });
                
                const data = await response.json();
                
                loading.style.display = 'none';
                
                if (data.success) {
                    document.getElementById('groupTitle').textContent = `Group: ${data.group_title}`;
                    document.getElementById('messagesScanned').textContent = data.messages_scanned;
                    document.getElementById('totalCount').textContent = data.total_count;
                    
                    const usernameList = document.getElementById('usernameList');
                    usernameList.innerHTML = '';
                    
                    if (data.usernames.length > 0) {
                        data.usernames.forEach(user => {
                            const div = document.createElement('div');
                            div.className = 'username-item';
                            div.innerHTML = `
                                <div class="username-info">
                                    <strong>@${user.username}</strong>
                                    ${user.first_name || user.last_name ? `<br><small>${user.first_name} ${user.last_name}</small>` : ''}
                                    <br><small>Last message: ${user.last_message_date}</small>
                                </div>
                                <div class="message-count">${user.message_count} msgs</div>
                            `;
                            usernameList.appendChild(div);
                        });
                    } else {
                        usernameList.innerHTML = '<p>No usernames found in the scanned messages.</p>';
                    }
                    
                    success.style.display = 'block';
                } else {
                    error.textContent = `Error: ${data.error}`;
                    error.style.display = 'block';
                }
            } catch (err) {
                loading.style.display = 'none';
                error.textContent = `Network error: ${err.message}`;
                error.style.display = 'block';
            }
            
            scrapeBtn.disabled = false;
            scrapeBtn.textContent = 'Scrape Messages';
        });
    </script>
</body>
</html>
'''

# --- Routes ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/scrape', methods=['POST'])
def scrape_group():
    global last_request_time
    
    # Simple rate-limiting
    current_time = datetime.now()
    if (current_time - last_request_time).total_seconds() < rate_limit_seconds:
        return jsonify({
            'success': False,
            'error': f'Rate limit exceeded. Please wait {rate_limit_seconds} seconds between requests.'
        }), 429
    
    last_request_time = current_time

    try:
        data = request.get_json()
        
        if not data or 'group_link' not in data:
            return jsonify({'success': False, 'error': 'Missing group_link in request body'}), 400
        
        group_link = data['group_link'].strip()
        time_limit_hours = data.get('time_limit_hours', 0)
        
        if group_link.startswith('https://t.me/'):
            group_username = group_link.replace('https://t.me/', '')
        elif group_link.startswith('@'):
            group_username = group_link[1:]
        else:
            group_username = group_link
        
        # Use a threading lock to prevent multiple scrapes from interfering with each other
        with scraper_lock:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(scraper.scrape_usernames_from_messages(group_username, time_limit_hours=time_limit_hours))
            loop.close()
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'message': 'Telegram Message Scraper API is running'})

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Starting Telegram Message History Scraper")
    print("=" * 60)
    print("📋 SETUP INSTRUCTIONS:")
    print("1. Make sure you have your own Telegram API credentials")
    print("2. Replace api_id, api_hash, and phone_number with your values")
    print("3. Install required packages: pip install telethon flask flask-cors")
    print("4. Run this script and visit http://localhost:5000")
    print("=" * 60)
    print("⚠️  IMPORTANT: This script will prompt for Telegram verification")
    print("   on first run. Follow the prompts to authorize your account.")
    print("=" * 60)
    
    try:
        app.run(debug=True, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
    except Exception as e:
        print(f"❌ Error starting server: {e}")

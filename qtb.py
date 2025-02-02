import requests
from bs4 import BeautifulSoup
import pandas as pd
import sqlite3
import schedule
import time
import json
from twilio.rest import Client
from telegram import Bot

# Load configuration from config.json
def load_config():
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: config.json not found.")
        return {}

config = load_config()

# Initialize Telegram Bot
TELEGRAM_TOKEN = "your_telegram_bot_token"
TELEGRAM_CHAT_ID = "your_telegram_chat_id"
telegram_bot = Bot(token=TELEGRAM_TOKEN)

# Function to fetch data from Dexscreener
def fetch_dexscreener_data():
    url = "https://dexscreener.com/ethereum"  # Replace with the desired chain
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')

    coins = []
    for row in soup.select('tr[tokenpair-row]'):
        try:
            pair = row.find('a', class_='pair-name').text.strip()
            price = row.find('span', class_='price').text.strip()
            volume = row.find('span', class_='volume').text.strip()
            change = row.find('span', class_='change').text.strip()
            token_address = row.find('a', href=True)['href'].split('/')[-1]  # Extract token address
            coins.append({
                "pair": pair,
                "price": price,
                "volume": volume,
                "change": change,
                "token_address": token_address
            })
        except Exception as e:
            print(f"Error parsing row: {e}")
    return coins

# Apply filters based on config
def apply_filters(coins, config):
    filtered_coins = []
    for coin in coins:
        try:
            price = float(coin['price'].replace('$', '').replace(',', ''))
            volume = float(coin['volume'].replace('$', '').replace(',', ''))
            change = float(coin['change'].strip('%'))

            if (price >= config['filters']['min_price'] and
                volume >= config['filters']['min_volume'] and
                change <= config['filters']['max_change']):
                filtered_coins.append(coin)
        except Exception as e:
            print(f"Error applying filters: {e}")
    return filtered_coins

# Check coin blacklist
def check_coin_blacklist(coins, config):
    blacklist = config.get('coin_blacklist', [])
    return [coin for coin in coins if coin['pair'].split('/')[0] not in blacklist]

# Check dev blacklist
def check_dev_blacklist(coins, config):
    blacklist = config.get('dev_blacklist', [])
    return [coin for coin in coins if coin.get('creator') not in blacklist]

# RugCheck.xyz integration
RUGCHECK_API_KEY = "your_rugcheck_api_key"

def check_token_on_rugcheck(token_address):
    headers = {
        "Authorization": f"Bearer {RUGCHECK_API_KEY}",
        "Content-Type": "application/json"
    }
    url = f"https://api.rugcheck.xyz/v2/token/{token_address}"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        result = response.json()
        status = result.get('status', 'Unknown')
        supply = result.get('supply', {})
        return status, supply
    else:
        print(f"Error checking token on RugCheck: {response.status_code} - {response.text}")
        return "Unknown", {}

# Filter tokens using RugCheck
def filter_tokens_with_rugcheck(coins):
    filtered_coins = []
    for coin in coins:
        try:
            token_address = coin.get('token_address')
            if not token_address:
                continue

            status, _ = check_token_on_rugcheck(token_address)
            if status == "Good":
                filtered_coins.append(coin)
            elif status == "Bundled":
                print(f"Coin {coin['pair']} is bundled. Adding to blacklist.")
                config['coin_blacklist'].append(coin['pair'].split('/')[0])
                config['dev_blacklist'].append(token_address)
        except Exception as e:
            print(f"Error filtering tokens with RugCheck: {e}")
    return filtered_coins

# Save updated blacklists
def save_config(config):
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

# Detect events
def detect_events(coins):
    events = []
    for coin in coins:
        if coin['pair'].split('/')[0] in config['coin_blacklist']:
            continue

        price = float(coin['price'].replace('$', '').replace(',', ''))
        change = float(coin['change'].strip('%'))
        volume = float(coin['volume'].replace('$', '').replace(',', ''))

        if price < config['filters']['min_price']:
            events.append({"event": "Rug Pull", "details": coin})
        elif change > 50:
            events.append({"event": "Pump", "details": coin})
        elif volume > 1_000_000:
            events.append({"event": "Tier-1", "details": coin})
    return events

# Send Telegram notification
def send_telegram_notification(message):
    try:
        telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        print(f"Notification sent: {message}")
    except Exception as e:
        print(f"Error sending Telegram notification: {e}")

# Use BonkBot to trade
def trade_with_bonkbot(action, token_address, amount):
    message = f"/{action} {token_address} {amount}"
    send_telegram_notification(message)

# Save data to SQLite database
def save_to_db(coins):
    conn = sqlite3.connect('coins.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS coins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pair TEXT,
        price REAL,
        volume REAL,
        change REAL,
        token_address TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    for coin in coins:
        cursor.execute('''
        INSERT INTO coins (pair, price, volume, change, token_address)
        VALUES (?, ?, ?, ?, ?)
        ''', (coin['pair'], coin['price'], coin['volume'], coin['change'], coin['token_address']))
    conn.commit()
    conn.close()

# Main job function
def job():
    global config
    data = fetch_dexscreener_data()
    filtered_data = apply_filters(data, config)
    filtered_data = check_coin_blacklist(filtered_data, config)
    filtered_data = check_dev_blacklist(filtered_data, config)
    filtered_data = filter_tokens_with_rugcheck(filtered_data)
    save_config(config)  # Save updated blacklists
    events = detect_events(filtered_data)
    save_to_db(filtered_data)

    for event in events:
        details = event['details']
        message = f"Alert: {event['event']} detected for {details['pair']}"
        send_telegram_notification(message)

        # Trade logic (example: buy on pump, sell on rug pull)
        if event['event'] == "Pump":
            trade_with_bonkbot("buy", details['token_address'], 0.1)  # Example: Buy $0.1 worth
        elif event['event'] == "Rug Pull":
            trade_with_bonkbot("sell", details['token_address'], "all")  # Example: Sell all

# Schedule the job every 5 minutes
schedule.every(5).minutes.do(job)

while True:
    schedule.run_pending()
    time.sleep(1)

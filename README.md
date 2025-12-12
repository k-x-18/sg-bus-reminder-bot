SG Bus Reminder Bot for Telegram

A Telegram bot that allows users to set scheduled bus arrival reminders for Singapore bus services using the LTA DataMall APIs. Users can enter a bus service, specify a bus stop using either a stop code or stop name, and schedule reminders for weekdays or everyday.


Features:

- Set bus arrival reminders by bus number and stop (code or name)
- Supports weekday-only or daily reminders
- Time-based reminders (24-hour HH:MM format)
- Validates that a bus stop belongs to the selected bus service
- Uses LTA Bus Routes, Bus Stops, and Bus Arrival APIs
- In-memory caching
- Planned deployment on AWS EC2 (Free Tier)

Tech Stack:

- Python 3
- python-telegram-bot
- LTA DataMall APIs
- requests, python-dotenv

Setup:

Create a .env file in project root:

TELEGRAM_BOT_TOKEN=your_telegram_bot_token
LTA_API_KEY=your_lta_datamall_api_key

Install Dependencies
pip install -r requirements.txt

Run the Bot
python bot.py

Bot Commands:

/start – Start the bot
/help – Show help menu
/setbusreminder – Create a new reminder
/list – List all reminders
/deletereminder <number> – Delete a reminder

Notes:
- Reminders are stored in memory and will be lost if the bot restarts.

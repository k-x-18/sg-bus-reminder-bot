# SG Bus Reminder Bot for Telegram

A Telegram bot that allows users to set scheduled bus arrival reminders for Singapore bus services using the LTA DataMall APIs. Users can enter a bus service, specify a bus stop using either a stop code or stop name, and schedule reminders for weekdays or everyday.

## Features

- Set bus arrival reminders by bus number and stop (code or name)
- Supports weekday-only or daily reminders
- Time-based reminders (24-hour HH:MM format)
- Validates that a bus stop belongs to the selected bus service
- Uses LTA Bus Routes, Bus Stops, and Bus Arrival APIs
- Persistent storage with AWS DynamoDB
- In-memory caching for bus stops and routes
- Designed for AWS deployment (EC2; adaptable to Lambda or Fargate)

## Tech Stack

- Python 3.8+
- python-telegram-bot
- AWS DynamoDB (boto3)
- LTA DataMall APIs
- requests, python-dotenv

## Deployment

The bot is deployed on AWS EC2 and runs continuously using `systemd`.

- Uses IAM role–based access for DynamoDB (no credentials stored in code)
- DynamoDB table is created automatically at startup if not present
- Designed to operate within AWS Free Tier limits

## Local Development:

1. **Create a `.env` file** in project root:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
LTA_API_KEY=your_lta_datamall_api_key
DYNAMODB_TABLE_NAME=sg-bus-reminders
AWS_REGION=ap-southeast-1
```

2. **Install Dependencies**:

```bash
pip install -r requirements.txt
```

3. **Run the Bot**:
```bash
python bot.py
```
DynamoDB tables are created automatically at startup if permissions are available.


## Bot Commands:

- `/start` – Start the bot
- `/help` – Show help menu
- `/setbusreminder` – Create a new reminder
- `/list` – List all reminders
- `/buslist` – Show a paginated list of bus stops for the currently selected bus  
  (Available during `/setbusreminder` to help users find the correct stop code)
- `/deletereminder <number>` – Delete a reminder
- `/cancel` – Cancel the current reminder setup

## Notes:

- Reminders are now stored in DynamoDB and persist across bot restarts
- Bus stops and routes are cached in memory for performance

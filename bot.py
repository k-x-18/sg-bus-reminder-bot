import os
import logging
import re
import requests
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler

# Load environment variables from .env file
load_dotenv()

ASK_BUS_NUMBER, ASK_BUS_STOP, ASK_DAYS, ASK_TIME = range(4)

# Enable logging with structured format
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

#====================================================================GLOBAL CACHES============================================================
# Global caches for performance optimization
bus_route_cache = {}          # service_no → list of route entries
all_bus_stops_cache = {}     # stop_code → {code, name, road}
user_reminders = {}          # chat_id → list of reminders

#====================================================================API CONFIGURATION============================================================
LTA_API_URL_BUSROUTES = "https://datamall2.mytransport.sg/ltaodataservice/BusRoutes"
LTA_API_URL_BUSSTOPS = "https://datamall2.mytransport.sg/ltaodataservice/BusStops"
LTA_API_URL_BUSARRIVAL = "https://datamall2.mytransport.sg/ltaodataservice/v3/BusArrival"
LTA_API_KEY = os.getenv('LTA_API_KEY')
API_TIMEOUT = 5
MAX_RETRIES = 3

#====================================================================API CALLS============================================================

def load_all_bus_stops():
    """
    Loads all bus stops from the LTA BusStops API into all_bus_stops_cache.
    Perform pagination once at startup.
    Cache format:
        code → {
            "code": "28009",
            "name": "Jurong East Int",
            "road": "Jurong East Ctrl"
        }
    """
    headers = {
        "AccountKey": LTA_API_KEY,
        "accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    
    skip = 0
    batch_size = 500
    total_loaded = 0
    
    logger.info("Starting to load all bus stops into cache", extra={"service": "", "count": 0, "endpoint": "BusStops", "status": ""})
    
    while True:
        params = {
            "$top": batch_size,
            "$skip": skip
        }
        
        try:
            resp = requests.get(LTA_API_URL_BUSSTOPS, headers=headers, params=params, timeout=API_TIMEOUT)
            
            if resp.status_code != 200:
                logger.error("API failure", extra={"service": "", "count": total_loaded, "endpoint": "BusStops", "status": resp.status_code})
                logger.error(resp.text[:300])
                break
            
            try:
                data = resp.json()
            except (ValueError, requests.exceptions.JSONDecodeError) as e:
                logger.error("Failed to decode BusStops JSON", extra={"service": "", "count": total_loaded, "endpoint": "BusStops", "status": resp.status_code})
                logger.error(resp.text[:300])
                break
            
            stops = data.get("value", [])
            if not stops:
                break
            
            # Cache all stops
            for stop in stops:
                code = str(stop.get("BusStopCode", ""))
                name = stop.get("Description", "") or stop.get("RoadName", "") or f"Stop {code}"
                road = stop.get("RoadName", "") or ""
                
                all_bus_stops_cache[code] = {
                    "code": code,
                    "name": name,
                    "road": road
                }
                total_loaded += 1
            
            logger.info("Loaded bus stops batch", extra={"service": "", "count": len(stops), "endpoint": "BusStops", "status": resp.status_code})
            
            # If we got fewer than batch_size results, we've reached the end
            if len(stops) < batch_size:
                break
            
            skip += batch_size
            
        except requests.exceptions.Timeout:
            logger.error("API timeout", extra={"service": "", "count": total_loaded, "endpoint": "BusStops", "status": "timeout"})
            break
        except requests.exceptions.RequestException as e:
            logger.error(f"API request error: {e}", extra={"service": "", "count": total_loaded, "endpoint": "BusStops", "status": "error"})
            break
    
    logger.info("Finished loading all bus stops", extra={"service": "", "count": total_loaded, "endpoint": "BusStops", "status": ""})

def get_bus_routes(service_no: str):
    """Retrieve all bus routes for a given service number, handling pagination with caching."""
    # Check cache first
    if service_no in bus_route_cache:
        logger.info("Loaded bus routes from cache", extra={"service": service_no, "count": len(bus_route_cache[service_no]), "endpoint": "BusRoutes", "status": ""})
        return bus_route_cache[service_no]
    
    headers = {
        "AccountKey": LTA_API_KEY,
        "accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    all_routes = []
    skip = 0
    batch_size = 500
    
    while True:
        # Try with filter first
        params = {
            "$filter": f"ServiceNo eq '{service_no}'",
            "$top": batch_size,
            "$skip": skip
        }

        # Retry logic for API calls
        resp = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(LTA_API_URL_BUSROUTES, headers=headers, params=params, timeout=API_TIMEOUT)
                break
            except requests.exceptions.Timeout:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(f"API timeout, retrying... (attempt {attempt + 1}/{MAX_RETRIES})", extra={"service": service_no, "count": len(all_routes), "endpoint": "BusRoutes", "status": "timeout"})
                    time.sleep(1)
                else:
                    logger.error("API timeout after retries", extra={"service": service_no, "count": len(all_routes), "endpoint": "BusRoutes", "status": "timeout"})
                    return []
            except requests.exceptions.RequestException as e:
                logger.error(f"API request error: {e}", extra={"service": service_no, "count": len(all_routes), "endpoint": "BusRoutes", "status": "error"})
                return []
        
        if resp is None:
            return []
        
        # Check if the request was successful
        if resp.status_code != 200:
            logger.error("API failure", extra={"service": service_no, "count": len(all_routes), "endpoint": "BusRoutes", "status": resp.status_code})
            logger.error(resp.text[:300])
            break

        try:
            data = resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError) as e:
            logger.error("Failed to decode BusRoutes JSON", extra={"service": service_no, "count": len(all_routes), "endpoint": "BusRoutes", "status": resp.status_code})
            logger.error(resp.text[:300])
            break

        routes = data.get("value", [])
        if not routes:
            # No more routes to fetch
            break
        
        # Filter to ensure we only get routes for this service number (double-check)
        filtered = [r for r in routes if str(r.get("ServiceNo", "")) == service_no]
        all_routes.extend(filtered)
        
        logger.info("Retrieved bus routes batch from API", extra={"service": service_no, "count": len(filtered), "endpoint": "BusRoutes", "status": resp.status_code})
        
        # If we got fewer than batch_size results, we've reached the end
        if len(routes) < batch_size:
            break
        
        skip += batch_size
    
    # Cache the results
    bus_route_cache[service_no] = all_routes
    logger.info("Loaded bus routes", extra={"service": service_no, "count": len(all_routes), "endpoint": "BusRoutes", "status": ""})
    return all_routes


#====================================================================BUS ARRIVAL API============================================================

def get_bus_arrival(stop_code: str, bus_number: str):
    headers = {
        "AccountKey": LTA_API_KEY,
        "accept": "application/json",
        "User-Agent": "Mozilla/5.0"
    }

    params = {"BusStopCode": stop_code}

    try:
        resp = requests.get(LTA_API_URL_BUSARRIVAL, headers=headers, params=params, timeout=API_TIMEOUT)

        if resp.status_code != 200:
            logger.error(f"BusArrival API failure {resp.status_code}", extra={"service": bus_number, "count": 0, "endpoint": "BusArrival", "status": resp.status_code})
            return None

        data = resp.json()

        for svc in data.get("Services", []):
            if svc.get("ServiceNo") == bus_number:
                return svc

        return None

    except Exception as e:
        logger.error(f"BusArrival API error: {e}", extra={"service": bus_number, "count": 0, "endpoint": "BusArrival", "status": "error"})
        return None


#====================================================================CACHE-BASED FUNCTIONS (NO API CALLS)============================================================

def is_stop_in_bus_route(bus_number: str, stop_code: str) -> bool:
    """Check if a bus stop is in a bus service route using cached routes."""
    routes = get_bus_routes(bus_number)
    route_stop_codes = {str(r.get("BusStopCode", "")) for r in routes}
    return stop_code in route_stop_codes

def search_bus_stops_by_name(query: str) -> list:
    """
    Performs case-insensitive substring matching on cached bus stops.
    Returns a list of stop dicts.
    No API calls.
    """
    query = query.lower()
    matches = []
    
    for stop in all_bus_stops_cache.values():
        if query in stop["name"].lower() or query in stop["road"].lower():
            matches.append(stop)
    
    return matches

def get_bus_stop_name(code: str):
    """Get bus stop name from cache. Returns None if not found. No API calls."""
    stop = all_bus_stops_cache.get(code)
    return stop["name"] if stop else None


#====================================================================HELPERS============================================================

def minutes_to_arrival(iso_time: str):
    if not iso_time:
        return None

    try:
        arr = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = (arr - now).total_seconds() / 60
        return max(0, int(diff))
    except Exception:
        return None


def format_arrival_message(service, bus_number):
    next_bus = service.get("NextBus", {}) if service else {}
    next2_bus = service.get("NextBus2", {}) if service else {}

    next_arrival = minutes_to_arrival(next_bus.get("EstimatedArrival"))
    next2_arrival = minutes_to_arrival(next2_bus.get("EstimatedArrival"))

    msg = f"🚌 *Bus {bus_number} Arrival Info*\\n"

    if next_arrival is None:
        msg += "No arrival data available.\\n"
        return msg

    if next_arrival <= 2:
        urgency = "*RUN! The bus is arriving soon!*"
    elif next_arrival <= 5:
        urgency = "*Bus is coming shortly*"
    else:
        urgency = "You have some time."

    msg += f"Next bus: *{next_arrival} min*\\n"
    msg += f"Second bus: *{next2_arrival} min*\\n\\n"
    msg += urgency

    return msg


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    sg_now = datetime.now(timezone.utc).astimezone()  # uses system local TZ; adjust here if needed
    now = sg_now.strftime("%H:%M")
    weekday = sg_now.weekday()  # Monday=0

    for chat_id, reminders in user_reminders.items():
        for rem in reminders:
            # Skip weekdays-only reminders on weekends
            if rem.get("days") == "weekdays" and weekday >= 5:
                continue

            if rem.get("time") == now:
                bus = rem.get("bus_number")
                stop = rem.get("bus_stop")

                service = get_bus_arrival(stop, bus)

                if not service:
                    await context.bot.send_message(chat_id, f"Could not fetch arrival for Bus {bus}")
                    continue

                msg = format_arrival_message(service, bus)

                await context.bot.send_message(chat_id, msg, parse_mode="Markdown")


def validate_bus_stop_input(bus_number: str, user_input: str):
    """
    Validate bus stop input (code or name).
    Returns: {code, name} if valid, None if invalid, or error message string.
    No API calls - uses cache only.
    """
    user_input = user_input.strip()
    
    # Case A: User entered a 5-digit code
    if user_input.isdigit() and len(user_input) == 5:
        stop_code = user_input
        
        # Check if stop exists in cache
        stop = all_bus_stops_cache.get(stop_code)
        if not stop:
            # Stop not found
            return None
        
        # Check if stop is in bus route
        if not is_stop_in_bus_route(bus_number, stop_code):
            return None
        
        return {"code": stop_code, "name": stop["name"]}
    
    # Case B: User entered a NAME
    matches = search_bus_stops_by_name(user_input)
    
    if len(matches) == 0:
        return None
    elif len(matches) > 1:
        # Multiple matches - return error message
        return f"I found multiple stops matching \"{user_input}\".\nTry entering the exact 5-digit bus stop code."
    else:
        # One match - validate it belongs to the bus route
        match = matches[0]
        stop_code = match["code"]
        
        if not is_stop_in_bus_route(bus_number, stop_code):
            return None
        
        return match


#====================================================================BOT COMMANDS=======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_text(
        f'Hello {user.first_name}! Welcome to the bus reminder bot.\n'
        'Use /help to see available commands.'
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    help_text = """
Available commands:
/start - Start the bot
/help - Show this help message
/setbusreminder - Set a bus reminder
/list - List all bus reminders
/deletereminder <number> - Delete a reminder by its number in /list output
/cancel - Cancel the current reminder setup
    """
    await update.message.reply_text(help_text)

async def set_bus_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Which bus number? (e.g. 970)")
    return ASK_BUS_NUMBER

async def ask_bus_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bus_number = update.message.text.strip()
    context.user_data["bus_number"] = bus_number

    routes = get_bus_routes(bus_number)

    if not routes:
        await update.message.reply_text(
            f"Bus {bus_number} does not exist!\n"
            f"Please try again with a valid bus number."
        )
        return ASK_BUS_NUMBER

    await update.message.reply_text(
        "Please enter the 5 digit bus stop code or the stop name (e.g. Jurong East Int)"
    )

    return ASK_BUS_STOP

async def validate_and_process_bus_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate bus stop input and proceed to next step."""
    user_input = update.message.text.strip()
    bus_number = context.user_data.get("bus_number")
    
    if not bus_number:
        await update.message.reply_text("Error: Bus number not found. Please start over with /setbusreminder")
        return ConversationHandler.END
    
    # Validate the input
    result = validate_bus_stop_input(bus_number, user_input)
    
    if result is None:
        await update.message.reply_text(
            f"Invalid bus stop or stop not found on Bus {bus_number}.\n"
            f"Please enter a valid 5-digit bus stop code or stop name."
        )
        return ASK_BUS_STOP
    
    if isinstance(result, str):
        # Error message (multiple matches)
        await update.message.reply_text(result)
        return ASK_BUS_STOP
    
    # Valid stop found
    stop_code = result["code"]
    stop_name = result["name"]
    context.user_data["bus_stop_code"] = stop_code
    context.user_data["bus_stop_name"] = stop_name
    
    # Proceed to ask for days
    days_buttons = [
        [InlineKeyboardButton("Weekdays", callback_data="weekdays")],
        [InlineKeyboardButton("Everyday", callback_data="everyday")]
    ]
    
    await update.message.reply_text(
        f"Bus stop confirmed: {stop_name} ({stop_code})\n\n"
        f"When should I send reminders?",
        reply_markup=InlineKeyboardMarkup(days_buttons)
    )
    
    return ASK_DAYS
 

async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data["selected_days"] = query.data
    await query.edit_message_text("What time? (HH:MM)")

    return ASK_TIME

async def save_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text.strip()

    # Validate time format HH:MM (24hr)
    if not re.match(r"^\d{2}:\d{2}$", time_str):
        await update.message.reply_text(
            "Invalid time format!\n"
            "Please enter in HH:MM (24-hour) format.\n"
            "Example: 07:30 or 18:45"
        )
        return ASK_TIME

    # Validate it is a real time
    try:
        datetime.strptime(time_str, "%H:%M")
    except ValueError:
        await update.message.reply_text(
            "⏱ That doesn't seem like a real time.\n"
            "Try again (HH:MM, 24-hour format)."
        )
        return ASK_TIME

    # Save time since valid
    context.user_data["time"] = time_str

    bus = context.user_data.get("bus_number")
    stop_code = context.user_data.get("bus_stop_code")
    stop_name = context.user_data.get("bus_stop_name") or get_bus_stop_name(context.user_data.get("bus_stop_code"))
    days = context.user_data.get("selected_days")

    chat_id = update.effective_chat.id
    if chat_id not in user_reminders:
        user_reminders[chat_id] = []

    user_reminders[chat_id].append({
        "bus_number": bus,
        "bus_stop": stop_code,
        "bus_stop_name": stop_name,
        "days": days,
        "time": time_str
    })

    await update.message.reply_text(
        f"Reminder saved!\n\n"
        f"Bus: *{bus}*\n"
        f"Stop: *{stop_name} ({stop_code})*\n"
        f"Days: *{days}*\n"
        f"Time: *{time_str}*",
        parse_mode="Markdown"
    )

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Reminder setup cancelled.")
    #TODO implement cancel logic
    return ConversationHandler.END

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    reminders = user_reminders.get(chat_id, [])

    if not reminders:
        await update.message.reply_text("You don’t have any bus reminders set yet!")
        return

    response = "*Your Bus Reminders:*\n\n"
    for idx, r in enumerate(reminders, start=1):
        stop_code = r.get("bus_stop")
        stop_name = r.get("bus_stop_name") or get_bus_stop_name(stop_code) or stop_code
        response += (
            f"{idx}. {r['bus_number']} | "
            f"{stop_name} ({stop_code}) | "
            f"{r['days']} | {r['time']}\n"
        )

    await update.message.reply_text(response, parse_mode="Markdown")


async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a reminder by its number in /list output."""
    chat_id = update.effective_chat.id
    reminders = user_reminders.get(chat_id, [])

    if not reminders:
        await update.message.reply_text("You have no reminders to delete.")
        return

    # Expecting: /deletereminder <number>
    args = context.args if hasattr(context, "args") else []
    if not args:
        await update.message.reply_text("Usage: /deletereminder <number>\nExample: /deletereminder 1")
        return

    try:
        idx = int(args[0])
    except ValueError:
        await update.message.reply_text("Please provide a valid reminder number. Example: /deletereminder 1")
        return

    if idx < 1 or idx > len(reminders):
        await update.message.reply_text(f"Please provide a number between 1 and {len(reminders)}.")
        return

    removed = reminders.pop(idx - 1)
    stop_code = removed.get("bus_stop")
    stop_name = removed.get("bus_stop_name") or get_bus_stop_name(stop_code) or stop_code

    await update.message.reply_text(
        f"Deleted reminder #{idx}:\n"
        f"Bus: {removed.get('bus_number')}\n"
        f"Stop: {stop_name} ({stop_code})\n"
        f"Days: {removed.get('days')}\n"
        f"Time: {removed.get('time')}"
    )

    # Clean up empty list to free memory
    if not reminders:
        user_reminders.pop(chat_id, None)


def main() -> None:
    """Start the bot."""
    # Get bot token from environment variable
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        logger.error("Please set it using: export TELEGRAM_BOT_TOKEN='your_token_here'")
        return
    
    # Load all bus stops into cache at startup
    logger.info("Loading all bus stops into cache at startup...")
    load_all_bus_stops()
    logger.info(f"Cache loaded with {len(all_bus_stops_cache)} bus stops", extra={"service": "", "count": len(all_bus_stops_cache), "endpoint": "", "status": ""})
    
    # Create the Application
    application = Application.builder().token(token).build()

    # Schedule reminder checks
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=5)
    
    # Conversation handler for setting bus reminder
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("setbusreminder", set_bus_reminder)],
        states={
            ASK_BUS_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_bus_stop)],
            ASK_BUS_STOP: [MessageHandler(filters.TEXT & ~filters.COMMAND, validate_and_process_bus_stop)],
            ASK_DAYS: [CallbackQueryHandler(ask_time)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_reminder)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_reminders))
    application.add_handler(CommandHandler("deletereminder", delete_reminder))

    application.add_handler(conv_handler)
    
    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()


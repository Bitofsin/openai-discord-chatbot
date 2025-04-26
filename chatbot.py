import os
import openai
import discord
import logging
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load configurations from environment variables
TOKEN            = os.environ["DISCORD_BOT_TOKEN"]
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
OPENAI_API_BASE  = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_MODEL     = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo-16k")
MAX_CACHE        = int(os.environ.get("MAX_CACHE", 5))
COOLDOWN_TIME    = int(os.environ.get("COOLDOWN_TIME", 2))
ROLE_ID          = int(os.environ["ROLE_ID"])

# Configure OpenAI client to point at any compatible endpoint
openai.api_key  = OPENAI_API_KEY
openai.api_base = OPENAI_API_BASE

# Data structures for caching, cooldown, and last interaction
user_message_cache      = defaultdict(deque)
user_cooldown           = defaultdict(int)
user_last_interaction   = defaultdict(lambda: {"time": datetime.min, "channel": None})

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

def split_message(content, limit=2000):
    """Split a message into chunks of a specified limit."""
    return [content[i:i+limit] for i in range(0, len(content), limit)]

async def send_large_message(channel, content):
    """Send content which may be longer than the discord character limit."""
    for chunk in split_message(content):
        await channel.send(chunk)

# Load the system message from file
with open('system_message.txt', 'r', encoding='utf-8') as f:
    SYSTEM_MESSAGE = f.read()

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name}')

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Determine if we should respond
    mentioned     = bot.user in message.mentions
    replied_to_bot = message.reference and message.reference.resolved.author.id == bot.user.id
    last_inter    = user_last_interaction[message.author.id]
    within_time   = datetime.utcnow() - last_inter["time"] <= timedelta(seconds=120)
    same_channel  = last_inter["channel"] == message.channel.id

    if not (mentioned or replied_to_bot or (within_time and same_channel)):
        return

    # Permission check
    role = message.guild.get_role(ROLE_ID)
    if not role or (role not in message.author.roles and not any(r >= role for r in message.author.roles)):
        await message.channel.send("Sorry, you don't have permission to interact with me!")
        return

    await chat_with_openai(message)
    user_last_interaction[message.author.id] = {"time": datetime.utcnow(), "channel": message.channel.id}

async def chat_with_openai(message):
    # Enforce cooldown
    if user_cooldown[message.author.id]:
        await message.channel.send("Please wait a bit before chatting again!")
        return

    # Keep typing indicator alive
    async def keep_typing(channel):
        while True:
            await channel.trigger_typing()
            await asyncio.sleep(5)

    typing_task = asyncio.create_task(keep_typing(message.channel))

    # Build message history
    messages = [{"role": "system", "content": SYSTEM_MESSAGE}]
    for role_, content in user_message_cache[message.author.id]:
        messages.append({"role": role_, "content": content})
    messages.append({"role": "user", "content": message.content})

    try:
        # Send to OpenAI-compatible API
        response = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=messages
        )
        typing_task.cancel()

        ai_text = response.choices[0].message.content
        await send_large_message(message.channel, ai_text)

        # Update cache
        if len(user_message_cache[message.author.id]) >= MAX_CACHE:
            user_message_cache[message.author.id].popleft()
        user_message_cache[message.author.id].append(("user", message.content))
        user_message_cache[message.author.id].append(("assistant", ai_text))

        # Start cooldown
        user_cooldown[message.author.id] = COOLDOWN_TIME
        bot.loop.create_task(cooldown_user(message.author.id))

    except Exception as e:
        typing_task.cancel()
        logger.error(f"Error during OpenAI call: {e}")
        await message.channel.send(f"An error occurred: {e}")

async def cooldown_user(user_id):
    await asyncio.sleep(COOLDOWN_TIME)
    user_cooldown[user_id] = 0

bot.run(TOKEN)

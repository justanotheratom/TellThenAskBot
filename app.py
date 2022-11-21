
import os
import asyncio
from pathlib import Path
import logging
import flask
import telebot
from telebot.async_telebot import AsyncTeleBot
import toml
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import replicate

#-------------------------------------------------------------------------------

app = flask.Flask(__name__)
app.config.from_file("config.toml", toml.load)

logger = telebot.logger
telebot.logger.setLevel(logging.INFO)

bot = AsyncTeleBot(app.config['TELEGRAM_API_TOKEN'])

WEBHOOK_URL_BASE = "https://%s" % (app.config['WEBHOOK_HOST'])
WEBHOOK_URL_PATH = "/%s/" % (app.config['TELEGRAM_API_TOKEN'])

os.environ['REPLICATE_API_TOKEN'] = app.config['REPLICATE_API_TOKEN']

#-------------------------------------------------------------------------------
# HELPERS
#-------------------------------------------------------------------------------

def transcribe(file_id):
    file_info = bot.get_file(file_id)
    file_content = bot.download_file(file_info.file_path)
    f = open('foo.ogg', 'w+b')
    f.write(file_content)
    f.close()
    model = replicate.models.get("openai/whisper")
    version = model.versions.get("089ea17a12d0b9fc2f81d620cc6e686de7a156007830789bf186392728ac25e8")
    result = version.predict(audio=Path('foo.ogg'))
    return result['transcription']

#-------------------------------------------------------------------------------
# ROUTES
#-------------------------------------------------------------------------------

@app.route('/', methods=['GET', 'HEAD'])
def index():
    return 'Hello from TellThenAskBot!'

@app.route(WEBHOOK_URL_PATH, methods=['POST'])
def webhook():
    if flask.request.headers.get('content-type') == 'application/json':
        json_string = flask.request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        flask.abort(403)

#-------------------------------------------------------------------------------
# COMMANDS
#-------------------------------------------------------------------------------

@bot.message_handler(commands=['start'])
async def send_welcome(message):
    await bot.send_message(
        message.chat.id,
        (
            "Hello human. Tell me things you know you might forget. "
            "Then later, ask me questions to refresh your memory.\n\n"
            "Commands:\n\n"
            "/deletealldata - Remove all personal data and start over.\n"
            "/givefeedback <feedback> - Send feedback to the developer.\n"
        )
    )

def gen_markup():
    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton(u"\U0001f44d", callback_data="cb_yes"),
        InlineKeyboardButton(u"\U0001f44e", callback_data="cb_no")
    )
    return markup

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    print(call)
    if call.data == "cb_yes":
        bot.answer_callback_query(call.id, "Ok, if you say so. Done.")
    elif call.data == "cb_no":
        bot.answer_callback_query(call.id, "Good choice. We all make mistakes.")

@bot.message_handler(commands=['deletealldata'])
async def delete_handler(message):
    markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(KeyboardButton('Yes, I confirm.'))
    await bot.send_message(
        message.chat.id,
        "What?? Are you sure you want to delete all your data?",
        reply_markup=markup
    )

@bot.message_handler(commands=['givefeedback'])
async def feedback(message):
    await bot.send_message(
        message.chat.id,
        (
            "Me so happy. Thank you for feedback."
        )
    )

#-------------------------------------------------------------------------------
# HANDLERS
#-------------------------------------------------------------------------------

@bot.message_handler(content_types=['text'])
async def echo_message(message):
    print('text')
    await bot.reply_to(message, message.text)

@bot.message_handler(content_types=['voice'])
async def audio_sink(message):
    await bot.send_message(
        message.chat.id,
        transcribe(message.voice.file_id))

@bot.message_handler(func=lambda message: True)
async def echo_message(message):
    await bot.reply_to(message, message.text)

#-------------------------------------------------------------------------------

asyncio.run(bot.run_webhooks(
    listen= app.config['WEBHOOK_LISTEN'],
    port= app.config['WEBHOOK_PORT'],
    url_path= WEBHOOK_URL_PATH,
    certificate= app.config['WEBHOOK_SSL_CERT'],
    certificate_key= app.config['WEBHOOK_SSL_PRIV'],
    webhook_url= WEBHOOK_URL_BASE + WEBHOOK_URL_PATH
))

#-------------------------------------------------------------------------------
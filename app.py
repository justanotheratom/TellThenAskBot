
import os
from pathlib import Path
import logging
import time
import flask
import telebot
import toml
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import replicate
from datetime import datetime
from dataclasses import dataclass
from collections import defaultdict
import tempfile
from dataclass_csv import DataclassReader, DataclassWriter
import openai

#-------------------------------------------------------------------------------

app = flask.Flask(__name__)
app.config.from_file("config.toml", toml.load)

logger = telebot.logger
telebot.logger.setLevel(logging.INFO)

bot = telebot.TeleBot(app.config['TELEGRAM_API_TOKEN'])

WEBHOOK_URL_BASE = "https://%s" % (app.config['WEBHOOK_HOST'])
WEBHOOK_URL_PATH = "/%s/" % (app.config['TELEGRAM_API_TOKEN'])

os.environ['REPLICATE_API_TOKEN'] = app.config['REPLICATE_API_TOKEN']
openai.api_key = app.config['OPENAI_API_TOKEN']

#-------------------------------------------------------------------------------

@dataclass
class JournalEntry:
    timestamp: int
    text: str
    voice_file_id: str

@dataclass
class QAEntry:
    timestamp: int
    question: str
    answer: str
    voice_file_id: str

@dataclass
class UserData:
    journal: list
    qa: list

userdata = {}

#-------------------------------------------------------------------------------
# PERSISTANCE LAYER
#-------------------------------------------------------------------------------

def user_data_directory(user_id):
    return os.path.join(app.config['DATA_DIRECTORY'], str(user_id))

def user_journal_file(user_id):
    return os.path.join(user_data_directory(user_id), 'journal.tsv')

def user_qa_file(user_id):
    return os.path.join(user_data_directory(user_id), 'qa.tsv')

def hydrate_user_data(user_id):
    if not user_id in userdata:
        userdata[user_id] = UserData([], [])
        journal_file = user_journal_file(user_id)
        if os.path.exists(journal_file):
            with open(journal_file, "r") as f:
                reader = DataclassReader(f, JournalEntry, delimiter='\t')
                for row in reader:
                    userdata[user_id].journal.append(row)
        qa_file = user_qa_file(user_id)
        if os.path.exists(qa_file):
            with open(qa_file, "r") as f:
                reader = DataclassReader(f, QAEntry, delimiter='\t')
                for row in reader:
                    userdata[user_id].qa.append(row)

#-------------------------------------------------------------------------------
# HELPERS
#-------------------------------------------------------------------------------

def transcribe(file_id):

    file_info = bot.get_file(file_id)
    file_content = bot.download_file(file_info.file_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path: Path = Path(tmp_dir) / Path('foo.ogg')
        with open(tmp_path, "w+b") as f:
            f.write(file_content)
        model = replicate.models.get(app.config['REPLICATE_MODEL_NAME'])
        version = model.versions.get(app.config['REPLICATE_MODEL_VERSION'])
        #print("Transcribing...")
        result = version.predict(audio=tmp_path, model='large')
        #print("Transcription complete.")
        return result['transcription']

def send_response(
    user_id,
    response,
    markup=telebot.types.ReplyKeyboardRemove(selective=False)
    ):
    bot.send_message(
        user_id,
        response,
        reply_markup=markup
    )

def is_question(text):
    return text.endswith('?')

def generate_answer(journal, question):
    prefix = \
        "Answer the question as truthfully as possible using the provided context, " + \
        "and if the answer is not contained within the text below, say \"I don't know.\"" + \
        "\n" + \
        "\n" + \
        "Context:" + \
        "\n" + \
        "\n"

    context = "\n".join([entry.text for entry in journal]) + \
        "\n" + \
        "\n"

    q = "Q: " + question

    suffix = "\n" + "A: "

    prompt = prefix + context + q + suffix

    #print("Completing...")
    completion = openai.Completion.create(
        prompt=prompt,
        temperature=0,
        max_tokens=10,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0,
        model=app.config['OPENAI_MODEL_NAME']
    )["choices"][0]["text"].strip(" \n")
    #print("Completion complete.")

    return completion

def process_question(user_id, qa_entry):
    hydrate_user_data(user_id)

    qa_entry.answer = generate_answer(userdata[user_id].journal, qa_entry.question)

    userdata[user_id].qa.append(qa_entry)
    os.makedirs(user_data_directory(user_id), exist_ok=True)
    qa_file = user_qa_file(user_id)
    if not os.path.exists(qa_file):
        with open(qa_file, "w") as f:
            w = DataclassWriter(f, [], QAEntry, delimiter='\t')
            w.write()
    with open(qa_file, 'a+') as f:
        w = DataclassWriter(f, [qa_entry], QAEntry, delimiter='\t')
        w.write(skip_header=True)

    send_response(user_id, qa_entry.answer)

def process_journal_entry(user_id, journal_entry):
    hydrate_user_data(user_id)
    userdata[user_id].journal.append(journal_entry)
    os.makedirs(user_data_directory(user_id), exist_ok=True)
    journal_file = user_journal_file(user_id)
    if not os.path.exists(journal_file):
        with open(journal_file, "w") as f:
            w = DataclassWriter(f, [], JournalEntry, delimiter='\t')
            w.write()
    with open(journal_file, 'a+') as f:
        w = DataclassWriter(f, [journal_entry], JournalEntry, delimiter='\t')
        w.write(skip_header=True)

def process_text(user_id, timestamp, text, voice_file_id): 
    if (is_question(text)):
        process_question(user_id, QAEntry(timestamp, text, None, voice_file_id))
    else:
        process_journal_entry(user_id, JournalEntry(timestamp, text, voice_file_id))

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
def send_welcome(message):
    send_response(
        message.from_user.id,
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
    if call.data == "cb_yes":
        bot.answer_callback_query(call.id, "Ok, if you say so. Done.")
    elif call.data == "cb_no":
        bot.answer_callback_query(call.id, "Good choice. We all make mistakes.")

@bot.message_handler(commands=['deletealldata'])
def send_welcome(message):
    markup = ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(KeyboardButton('Yes, I confirm.'))
    send_response(
        message.from_user.id,
        "What?? Are you sure you want to delete all your data?",
        markup
    )

@bot.message_handler(commands=['givefeedback'])
def send_welcome(message):
    send_response(
        message.from_user.id,
        (
            "Me so happy. Thank you for feedback."
        )
    )

#-------------------------------------------------------------------------------
# HANDLERS
#-------------------------------------------------------------------------------

@bot.message_handler(content_types=['text'])
def text_sink(message):
    process_text(
        message.from_user.id,
        message.date,
        message.text,
        "None"
    )

@bot.message_handler(content_types=['voice'])
def audio_sink(message):
    voice_file_id = message.voice.file_id
    text = transcribe(voice_file_id)
    send_response(message.from_user.id, text)
    process_text(
        message.from_user.id,
        message.date,
        text,
        voice_file_id
    )

#-------------------------------------------------------------------------------

# Remove webhook, sometimes the set_webhook fails if one was already set.
bot.remove_webhook()

time.sleep(0.1)

# Set webhook
bot.set_webhook(url=WEBHOOK_URL_BASE + WEBHOOK_URL_PATH)

# Start flask server
app.run(host=app.config['WEBHOOK_LISTEN'],
        port=app.config['WEBHOOK_PORT'],
        ssl_context=(app.config['WEBHOOK_SSL_CERT'], app.config['WEBHOOK_SSL_PRIV']))

#-------------------------------------------------------------------------------
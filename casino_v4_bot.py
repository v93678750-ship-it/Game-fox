"""
🦊 Game FOX Casino Bot — v4
• Без ngrok и callback-сервера!
• Опрашивает /service/history каждые 5 сек
• side="to_service" = пополнение от игрока
• Профиль, баланс, вывод, 8 игр
"""

import telebot
import requests
import random
import re
import logging
import threading
import time
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ════════════════════════════════════════════
#              КОНФИГУРАЦИЯ
# ════════════════════════════════════════════
BOT_TOKEN    = "8558262996:AAH8Q6lZ0Bdj0-S37zOMw1_WjB-v6_mdA1Q"
ACCESS_KEY   = "ea9bf7f5bfeac7566c65fbe371bd35c63f2e83cdce25141f"
SERVICE_ID   = "1234ba8eab83"
API_BASE     = "https://zoom-game.ru/api"
POLL_INTERVAL = 5   # секунд между проверками истории

TOPUP_TPL  = (
    "https://t.me/foxcoingame_bot/app?"
    "startapp=service_{sid}__sum_{sum}__lock_1"
    "&topup_sum={sum}&topup_lock=1"
)
TOPUP_FREE = f"https://t.me/foxcoingame_bot/app?startapp=service_{SERVICE_ID}"

MIN_BET      = 5
MAX_BET      = 10_000
MIN_WITHDRAW = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ════════════════════════════════════════════
#   БАЗА ДАННЫХ (в памяти)
# ════════════════════════════════════════════
users: dict        = {}   # user_id -> {balance, name, total_win, total_bet, games}
active_duels: dict = {}   # duel_id -> {...}
seen_tx: set       = set()  # unique_id уже обработанных переводов

def get_user(uid: int, name: str = "Игрок") -> dict:
    if uid not in users:
        users[uid] = {"balance": 0, "name": name,
                      "total_win": 0, "total_bet": 0, "games": 0}
    return users[uid]

def add_balance(uid: int, amount: int, name: str = "Игрок"):
    u = get_user(uid, name)
    u["balance"] += amount
    log.info(f"[+BAL] {uid} +{amount} FC → итого {u['balance']}")

def charge(uid: int, amount: int) -> bool:
    u = users.get(uid)
    if not u or u["balance"] < amount:
        return False
    u["balance"]   -= amount
    u["total_bet"] += amount
    u["games"]     += 1
    return True

def award(uid: int, amount: int):
    u = users.get(uid)
    if u:
        u["balance"]   += amount
        u["total_win"] += amount

# ════════════════════════════════════════════
#   FOXCOIN API
# ════════════════════════════════════════════
def fc_history(offset: int = 0, limit: int = 20) -> dict:
    """GET /api/service/history/{access_key}?offset=0&limit=20"""
    try:
        r = requests.get(
            f"{API_BASE}/service/history/{ACCESS_KEY}",
            params={"offset": offset, "limit": limit},
            timeout=10
        )
        return r.json()
    except Exception as e:
        log.error(f"fc_history error: {e}")
        return {"error": str(e)}

def fc_transfer(uid: int, amount: int) -> dict:
    """POST /api/service/transfer — выплата игроку"""
    try:
        r = requests.post(
            f"{API_BASE}/service/transfer",
            json={"access_key": ACCESS_KEY, "user_id": uid, "sum": amount},
            timeout=10
        )
        data = r.json()
        log.info(f"fc_transfer uid={uid} sum={amount}: {data}")
        return data
    except Exception as e:
        log.error(f"fc_transfer error: {e}")
        return {"error": str(e)}

def fc_stat() -> dict:
    try:
        r = requests.get(f"{API_BASE}/service/stat/{ACCESS_KEY}", timeout=10)
        data = r.json()
        log.info(f"fc_stat: {data}")
        return data
    except Exception as e:
        return {"error": str(e)}

def topup_url(amount: int) -> str:
    return TOPUP_TPL.format(sid=SERVICE_ID, sum=amount)

# ════════════════════════════════════════════
#   ПОЛЛЕР ИСТОРИИ — главный механизм зачисления
#   Каждые N секунд проверяет новые переводы
#   side="to_service" → пополнение от игрока
# ════════════════════════════════════════════
def history_poller():
    log.info("▶ History poller запущен")
    # При старте загружаем последние 50 транзакций в seen_tx
    # чтобы не зачислять старые переводы повторно
    resp = fc_history(offset=0, limit=50)
    if "data" in resp:
        for tx in resp["data"]:
            seen_tx.add(tx.get("unique_id"))
        log.info(f"[INIT] загружено {len(seen_tx)} старых транзакций")

    while True:
        try:
            resp = fc_history(offset=0, limit=20)
            if "data" not in resp:
                time.sleep(POLL_INTERVAL)
                continue

            for tx in resp["data"]:
                uid_tx = tx.get("unique_id")
                if not uid_tx or uid_tx in seen_tx:
                    continue
                seen_tx.add(uid_tx)

                side    = tx.get("side", "")
                user_id = tx.get("user_id")
                amount  = tx.get("sum", 0)

                # Нас интересуют только входящие (игрок → сервис)
                if side == "to_service" and user_id and amount > 0:
                    log.info(f"[TOPUP] user={user_id} sum={amount} tx={uid_tx}")
                    u = get_user(int(user_id))
                    add_balance(int(user_id), int(amount))
                    # Уведомляем игрока
                    try:
                        bot.send_message(
                            int(user_id),
                            f"✅ <b>Баланс пополнен!</b>\n\n"
                            f"💰 Зачислено: <b>+{amount} FC</b>\n"
                            f"💼 Баланс: <b>{users[int(user_id)]['balance']} FC</b>\n\n"
                            f"Удачной игры! 🦊"
                        )
                    except Exception as e:
                        log.warning(f"Не удалось уведомить {user_id}: {e}")

        except Exception as e:
            log.error(f"[POLLER] ошибка: {e}")

        time.sleep(POLL_INTERVAL)

# ════════════════════════════════════════════
#   ИГРОВЫЕ ДВИЖКИ
# ════════════════════════════════════════════
SLOT_SYMS    = ["🍒","🍋","🍊","⭐","💎","7️⃣","🃏"]
SLOT_WEIGHTS = [28,  24,  20,  14,  8,   4,   2  ]

def play_slots(bet: int):
    r = random.choices(SLOT_SYMS, SLOT_WEIGHTS, k=3)
    row = " ┃ ".join(r)
    if r[0]==r[1]==r[2]:
        mul = {"7️⃣":10,"💎":5,"⭐":3}.get(r[0], 2)
        return True, row, mul, int(bet*mul)
    if r[0]==r[1] or r[1]==r[2]:
        return True, row, 1.5, int(bet*1.5)
    return False, row, 0, 0

def play_flip(choice: str):
    result = random.choice(["орёл","решка"])
    return choice==result, ("🦅 ОРЁЛ" if result=="орёл" else "🪙 РЕШКА")

DICE_E = ["","1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
def play_dice(guess: int):
    r = random.randint(1,6)
    return r==guess, DICE_E[r], r

RED_NUMS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
def play_roulette(color: str):
    n = random.randint(0,36)
    if n==0:           actual,emoji,mul = "зелёное",f"🟢 0",14
    elif n in RED_NUMS: actual,emoji,mul = "красное",f"🔴 {n}",2
    else:              actual,emoji,mul = "чёрное",f"⚫ {n}",2
    return color==actual, emoji, mul if color==actual else 0

def play_hl(choice: str):
    n = random.randint(1,100)
    win = (n>50 if choice=="больше" else n<50)
    arr = "📈" if n>50 else ("📉" if n<50 else "⚖️")
    return win, f"{arr} {n}"

def _draw(): return random.choice([2,3,4,5,6,7,8,9,10,10,10,10,11])
def _hand():
    h = _draw()+_draw()
    return h-10 if h>21 else h

def play_bj():
    p = _hand()
    if p<17:
        p += _draw()
        if p>21: p-=10
    d = _hand()
    if p>21: return False,p,d,"перебор у тебя"
    if d>21: return True,p,d,"перебор у дилера"
    if p>d:  return True,p,d,"твоя рука старше"
    if p==d: return None,p,d,"ничья"
    return False,p,d,"рука дилера старше"

def play_mines(mines: int, bet: int):
    safe=25-mines; cells=0; mul=1.0
    for _ in range(safe):
        rem=25-cells
        if rem<=0: break
        if random.randint(1,rem)<=mines:
            return False,cells,round(mul,2),0
        cells+=1
        mul=round(mul*rem/(rem-mines),2)
        if mul>=15: break
    return True,cells,round(mul,2),int(bet*mul)

# ════════════════════════════════════════════
#   КЛАВИАТУРЫ
# ════════════════════════════════════════════
def kb_main():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add(
        KeyboardButton("🎰 слоты"),     KeyboardButton("🪙 монетка"),   KeyboardButton("🎲 кости"),
        KeyboardButton("🎡 рулетка"),   KeyboardButton("🃏 блэкджек"),  KeyboardButton("📈 больше"),
        KeyboardButton("⚔️ дуэль"),    KeyboardButton("💣 мины"),      KeyboardButton("👤 профиль"),
        KeyboardButton("💰 пополнить"), KeyboardButton("💸 вывод"),     KeyboardButton("❓ помощь"),
    )
    return kb

def kb_bets(prefix: str):
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(*[InlineKeyboardButton(f"{a} 🪙", callback_data=f"{prefix}:{a}") for a in [10,25,50,100,250,500,1000]])
    return kb

def kb_flip(bet):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🦅 Орёл", callback_data=f"flip:орёл:{bet}"),
           InlineKeyboardButton("🪙 Решка", callback_data=f"flip:решка:{bet}"))
    return kb

def kb_dice(bet):
    kb = InlineKeyboardMarkup(row_width=3)
    faces="⚀⚁⚂⚃⚄⚅"
    kb.add(*[InlineKeyboardButton(f"{faces[i-1]} {i}", callback_data=f"dice:{i}:{bet}") for i in range(1,7)])
    return kb

def kb_roulette(bet):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("🔴 Красное ×2",  callback_data=f"rl:красное:{bet}"),
           InlineKeyboardButton("⚫ Чёрное ×2",   callback_data=f"rl:чёрное:{bet}"),
           InlineKeyboardButton("🟢 Зелёное ×14", callback_data=f"rl:зелёное:{bet}"))
    return kb

def kb_hl(bet):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📈 Больше 50 ×2", callback_data=f"hl:больше:{bet}"),
           InlineKeyboardButton("📉 Меньше 50 ×2", callback_data=f"hl:меньше:{bet}"))
    return kb

def kb_duel(duel_id, bet):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton(f"⚔️ Принять вызов ({bet} 🪙)", callback_data=f"da:{duel_id}"),
           InlineKeyboardButton("❌ Отклонить", callback_data=f"dd:{duel_id}"))
    return kb

def kb_mines(bet):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(*[InlineKeyboardButton(f"💣 {m} мин", callback_data=f"mines:{m}:{bet}") for m in [3,5,10,15]])
    return kb

def kb_topup():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(*[InlineKeyboardButton(f"💳 {a} FC", url=topup_url(a)) for a in [10,25,50,100,250,500,1000,2000]])
    kb.add(InlineKeyboardButton("✏️ Своя сумма", url=TOPUP_FREE))
    return kb

def kb_profile(uid):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("💰 Пополнить",callback_data=f"prof:topup:{uid}"),
           InlineKeyboardButton("💸 Вывести",  callback_data=f"prof:withdraw:{uid}"),
           InlineKeyboardButton("🔄 Обновить", callback_data=f"prof:refresh:{uid}"))
    return kb

def kb_withdraw(uid):
    u = get_user(uid); bal = u["balance"]
    kb = InlineKeyboardMarkup(row_width=3)
    amounts = [a for a in [10,25,50,100,250,500,1000] if a<=bal]
    if amounts:
        kb.add(*[InlineKeyboardButton(f"{a} FC", callback_data=f"wd:{a}:{uid}") for a in amounts])
    if bal>0:
        kb.add(InlineKeyboardButton(f"💰 Всё ({bal} FC)", callback_data=f"wd:{bal}:{uid}"))
    return kb

# ════════════════════════════════════════════
#   ПАРСИНГ КОМАНД  "игра ставка"
# ════════════════════════════════════════════
ALIASES = {
    "слоты":"slots","слот":"slots","slots":"slots",
    "монетка":"flip","монета":"flip","flip":"flip",
    "орёл":"flip","решка":"flip",
    "кости":"dice","кубик":"dice","dice":"dice",
    "рулетка":"rl","рулет":"rl","roulette":"rl",
    "блэкджек":"bj","блек":"bj","blackjack":"bj","21":"bj",
    "больше":"hl","меньше":"hl",
    "дуэль":"duel","duel":"duel",
    "мины":"mines","мина":"mines","mines":"mines",
}

def parse_cmd(text: str):
    t = re.sub(r"fc","", text.strip().lower())
    m = re.match(r"^([^\d\s]+)\s*(\d+)$",t) or re.match(r"^(\d+)\s*([^\d\s]+)$",t)
    if not m: return None
    a,b = m.group(1),m.group(2)
    word,num = (a,b) if not a.isdigit() else (b,a)
    game = ALIASES.get(word.strip())
    if not game: return None
    return game, int(num)

def bet_ok(bet): return MIN_BET<=bet<=MAX_BET

def mention(user):
    name=(user.first_name or "Игрок")[:20]
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def bal_line(uid): return f"💼 Баланс: <b>{users.get(uid,{}).get('balance',0)} FC</b>"

def profile_text(uid, tg_user=None):
    u = get_user(uid, tg_user.first_name if tg_user else "Игрок")
    if tg_user: u["name"]=tg_user.first_name or "Игрок"
    profit = u["total_win"]-u["total_bet"]
    ps = f"+{profit}" if profit>=0 else str(profit)
    return (
        f"👤 <b>Профиль</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🏷 Имя:   <b>{u['name']}</b>\n"
        f"🆔 ID:    <code>{uid}</code>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💼 <b>Баланс: {u['balance']} FC</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎮 Игр:       <b>{u['games']}</b>\n"
        f"💸 Поставлено: <b>{u['total_bet']} FC</b>\n"
        f"🏆 Выиграно:   <b>{u['total_win']} FC</b>\n"
        f"📊 Прибыль:    <b>{ps} FC</b>\n"
        f"━━━━━━━━━━━━━━━━"
    )

def try_bet(uid, bet, cid) -> bool:
    u = get_user(uid)
    if u["balance"] < bet:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💳 Пополнить", callback_data=f"prof:topup:{uid}"))
        bot.send_message(cid,
            f"❌ Недостаточно FC!\n\n"
            f"💼 Твой баланс: <b>{u['balance']} FC</b>\n"
            f"💸 Нужно:       <b>{bet} FC</b>\n\n"
            f"Пополни баланс 👇", reply_markup=kb)
        return False
    return charge(uid, bet)

# ════════════════════════════════════════════
#   КОМАНДЫ БОТА
# ════════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    get_user(msg.from_user.id, msg.from_user.first_name)
    if msg.chat.type=="private":
        bot.send_message(msg.chat.id,
            "🦊 <b>Game FOX Casino</b>\n\n"
            "Игры только в <b>группах</b>!\n\n"
            "Здесь можешь пополнить и посмотреть профиль 👇",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("👤 Профиль", callback_data=f"prof:refresh:{msg.from_user.id}"),
                InlineKeyboardButton("💰 Пополнить", callback_data=f"prof:topup:{msg.from_user.id}")
            ))
        return
    bot.send_message(msg.chat.id,
        "🦊 <b>Game FOX Casino</b>\n\n"
        "Пиши <b>игра + ставка</b>:\n\n"
        "<code>слоты 100</code>  <code>монетка 50</code>  <code>рулетка 200</code>\n"
        "<code>кости 75</code>   <code>блэкджек 150</code>  <code>больше 100</code>\n"
        "<code>мины 200</code>   <code>дуэль 500</code>\n\n"
        "💰 Сначала пополни баланс!",
        reply_markup=kb_main())

@bot.message_handler(commands=["помощь","help"])
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ("❓ помощь","помощь"))
def cmd_help(msg):
    bot.send_message(msg.chat.id,
        "🦊 <b>Справка Game FOX Casino</b>\n\n"
        "<b>Формат:</b> <code>игра ставка</code>\n\n"
        "🎰 <b>слоты</b> — 3×7️⃣=×10  3×💎=×5  3×⭐=×3  2одинак=×1.5\n"
        "🪙 <b>монетка</b> — орёл/решка → ×2\n"
        "🎲 <b>кости</b> — угадай 1-6 → ×5\n"
        "🎡 <b>рулетка</b> — красное/чёрное ×2  /  зелёное ×14\n"
        "🃏 <b>блэкджек</b> — обыграй дилера → ×2\n"
        "📈 <b>больше/меньше</b> — 50/50 → ×2\n"
        "💣 <b>мины</b> — больше мин = выше ×\n"
        "⚔️ <b>дуэль</b> — победитель забирает всё\n\n"
        f"📌 Ставка: {MIN_BET}–{MAX_BET} FC\n"
        "👤 Профиль: кнопка <b>👤 профиль</b>\n"
        "💰 Пополнить: кнопка <b>💰 пополнить</b>\n"
        "💸 Вывод: кнопка <b>💸 вывод</b>",
        reply_markup=kb_main())

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ("👤 профиль","профиль","/профиль"))
def cmd_profile(msg):
    bot.send_message(msg.chat.id,
        profile_text(msg.from_user.id, msg.from_user),
        reply_markup=kb_profile(msg.from_user.id))

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ("💰 пополнить","пополнить","/пополнить"))
def cmd_topup(msg):
    bot.send_message(msg.chat.id,
        "💳 <b>Пополнение баланса</b>\n\n"
        "После перевода FC <b>автоматически</b> зачислятся на твой баланс в боте.\n\n"
        "Выбери сумму:", reply_markup=kb_topup())

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ("💸 вывод","вывод","/вывод"))
def cmd_withdraw(msg):
    uid=msg.from_user.id; u=get_user(uid, msg.from_user.first_name)
    if u["balance"]<MIN_WITHDRAW:
        bot.send_message(msg.chat.id,
            f"❌ Минимальный вывод: <b>{MIN_WITHDRAW} FC</b>\n"
            f"💼 Твой баланс: <b>{u['balance']} FC</b>"); return
    bot.send_message(msg.chat.id,
        f"💸 <b>Вывод FC</b>\n\nДоступно: <b>{u['balance']} FC</b>\nВыбери сумму:",
        reply_markup=kb_withdraw(uid))

# ════════════════════════════════════════════
#   ПЕРЕХВАТЧИК ИГР  "игра ставка"
# ════════════════════════════════════════════
GAME_MENU = {
    "🎰 слоты":   ("slots","🎰 Слоты",      "×1.5—×10"),
    "🪙 монетка": ("flip", "🪙 Монетка",     "орёл/решка → ×2"),
    "🎲 кости":   ("dice", "🎲 Кости",        "угадай 1-6 → ×5"),
    "🎡 рулетка": ("rl",   "🎡 Рулетка",      "красное/чёрное ×2 / зелёное ×14"),
    "🃏 блэкджек":("bj",   "🃏 Блэкджек",     "обыграй дилера → ×2"),
    "📈 больше":  ("hl",   "📈 Больше/Меньше","50/50 → ×2"),
    "⚔️ дуэль":  ("duel", "⚔️ Дуэль",        "победитель забирает всё"),
    "💣 мины":    ("mines","💣 Мины",          "больше мин = выше ×"),
}

@bot.message_handler(func=lambda m: m.text and m.text.lower() in GAME_MENU)
def handle_game_btn(msg):
    if msg.chat.type=="private":
        bot.send_message(msg.chat.id,"⚠️ Только в группах!"); return
    key,label,tip = GAME_MENU[msg.text.lower()]
    bot.send_message(msg.chat.id,
        f"<b>{label}</b>  <i>{tip}</i>\n\n"
        f"Или напиши: <code>{label.split()[-1].lower()} 100</code>\n\nСтавка:",
        reply_markup=kb_bets(key))

@bot.message_handler(func=lambda m: m.chat.type in ("group","supergroup") and bool(parse_cmd(m.text or "")))
def handle_game_cmd(msg):
    parsed=parse_cmd(msg.text or "")
    if not parsed: return
    game,bet=parsed
    user=msg.from_user; uid=user.id; cid=msg.chat.id; name=mention(user)
    get_user(uid, user.first_name)
    if not bet_ok(bet):
        bot.reply_to(msg,f"❌ Ставка от <b>{MIN_BET}</b> до <b>{MAX_BET}</b> FC"); return

    if game=="slots":
        if not try_bet(uid,bet,cid): return
        win,row,mul,prize=play_slots(bet)
        if win:
            award(uid,prize)
            bot.reply_to(msg,f"🎰 <b>СЛОТЫ</b>  {name}\n\n╔══ {row} ══╗\n\n🎉 <b>ВЫИГРЫШ ×{mul}</b>\n💰 {bet} → <b>+{prize} FC</b>\n{bal_line(uid)}")
        else:
            bot.reply_to(msg,f"🎰 <b>СЛОТЫ</b>  {name}\n\n╔══ {row} ══╗\n\n😔 Не повезло — <b>-{bet} FC</b>\n{bal_line(uid)}")

    elif game=="flip":
        bot.reply_to(msg,f"🪙 <b>Монетка</b>  {name}\nСтавка: <b>{bet} FC</b>\nВыбери сторону:",reply_markup=kb_flip(bet))

    elif game=="dice":
        bot.reply_to(msg,f"🎲 <b>Кости</b>  {name}\nСтавка: <b>{bet} FC</b>  (угадаешь → ×5)\nВыбери число:",reply_markup=kb_dice(bet))

    elif game=="rl":
        bot.reply_to(msg,f"🎡 <b>Рулетка</b>  {name}\nСтавка: <b>{bet} FC</b>\nВыбери цвет:",reply_markup=kb_roulette(bet))

    elif game=="bj":
        if not try_bet(uid,bet,cid): return
        result,p,d,reason=play_bj()
        if result is True:
            prize=bet*2; award(uid,prize)
            bot.reply_to(msg,f"🃏 <b>Блэкджек</b>  {name}\n\nТы: <b>{p}</b>  │  Дилер: <b>{d}</b>\n✅ {reason}\n\n🎉 <b>ПОБЕДА!</b>  {bet} → <b>+{prize} FC</b>\n{bal_line(uid)}")
        elif result is None:
            award(uid,bet)
            bot.reply_to(msg,f"🃏 <b>Блэкджек</b>  {name}\n\nТы: <b>{p}</b>  │  Дилер: <b>{d}</b>\n🤝 Ничья — возврат <b>{bet} FC</b>\n{bal_line(uid)}")
        else:
            bot.reply_to(msg,f"🃏 <b>Блэкджек</b>  {name}\n\nТы: <b>{p}</b>  │  Дилер: <b>{d}</b>\n❌ {reason}\n\n😔 Проигрыш — <b>-{bet} FC</b>\n{bal_line(uid)}")

    elif game=="hl":
        bot.reply_to(msg,f"📈 <b>Больше/Меньше</b>  {name}\nСтавка: <b>{bet} FC</b>",reply_markup=kb_hl(bet))

    elif game=="duel":
        duel_id=f"{cid}_{uid}"
        active_duels[duel_id]={"initiator_id":uid,"initiator_name":name,"bet":bet,"chat_id":cid}
        bot.reply_to(msg,
            f"⚔️ <b>ДУЭЛЬ!</b>\n\n{name} бросает вызов!\n💰 Ставка: <b>{bet} FC</b> с каждого\n\nКто смелый? 👇",
            reply_markup=kb_duel(duel_id,bet))

    elif game=="mines":
        bot.reply_to(msg,f"💣 <b>Мины</b>  {name}\nСтавка: <b>{bet} FC</b>\nВыбери кол-во мин:",reply_markup=kb_mines(bet))

# ════════════════════════════════════════════
#   CALLBACKS
# ════════════════════════════════════════════

# ── Профиль ────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("prof:"))
def cb_profile(call):
    parts=call.data.split(":"); action=parts[1]; uid=int(parts[2])
    if action=="refresh":
        u=get_user(uid,call.from_user.first_name); u["name"]=call.from_user.first_name or "Игрок"
        try:
            bot.edit_message_text(profile_text(uid,call.from_user),
                call.message.chat.id,call.message.message_id,reply_markup=kb_profile(uid))
        except: pass
    elif action=="topup":
        bot.send_message(call.message.chat.id,"💳 <b>Пополнение</b>\n\nВыбери сумму:",reply_markup=kb_topup())
    elif action=="withdraw":
        u=get_user(uid); bal=u["balance"]
        if bal<MIN_WITHDRAW:
            bot.answer_callback_query(call.id,f"Минимум {MIN_WITHDRAW} FC. У тебя {bal} FC",show_alert=True); return
        bot.send_message(call.message.chat.id,
            f"💸 <b>Вывод FC</b>\n\nДоступно: <b>{bal} FC</b>\nВыбери сумму:",
            reply_markup=kb_withdraw(uid))
    bot.answer_callback_query(call.id)

# ── Вывод ──────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("wd:"))
def cb_withdraw(call):
    parts=call.data.split(":"); amount=int(parts[1]); uid=int(parts[2])
    if call.from_user.id!=uid:
        bot.answer_callback_query(call.id,"❌ Это не твой вывод!",show_alert=True); return
    u=get_user(uid)
    if u["balance"]<amount:
        bot.answer_callback_query(call.id,f"Недостаточно FC! Баланс: {u['balance']}",show_alert=True); return
    u["balance"]-=amount
    tx=fc_transfer(uid,amount)
    if "error" not in tx:
        try:
            bot.edit_message_text(
                f"✅ <b>Вывод выполнен!</b>\n\n💸 Выведено: <b>{amount} FC</b>\n💼 Остаток: <b>{u['balance']} FC</b>\n\nFC поступят в кошелёк FoxCoin 🦊",
                call.message.chat.id,call.message.message_id)
        except: pass
    else:
        u["balance"]+=amount  # возврат при ошибке
        try:
            bot.edit_message_text(
                f"❌ <b>Ошибка вывода!</b>\n<code>{tx.get('error')}</code>\n\nДеньги возвращены.",
                call.message.chat.id,call.message.message_id)
        except: pass
    bot.answer_callback_query(call.id)

# ── Выбор ставки из панели ─────────────────
@bot.callback_query_handler(func=lambda c: bool(re.match(r"^(slots|flip|dice|rl|bj|hl|duel|mines):\d+$",c.data)) and c.data.count(":")==1)
def cb_panel_bet(call):
    game,bet_s=call.data.split(":"); bet=int(bet_s)
    user=call.from_user; uid=user.id; name=mention(user); cid=call.message.chat.id
    get_user(uid,user.first_name)
    if not bet_ok(bet):
        bot.answer_callback_query(call.id,f"Ставка {MIN_BET}–{MAX_BET} FC",show_alert=True); return

    if game=="flip":
        bot.edit_message_text(f"🪙 <b>Монетка</b>  {name}\nСтавка: <b>{bet} FC</b>\nВыбери сторону:",cid,call.message.message_id,reply_markup=kb_flip(bet))
    elif game=="dice":
        bot.edit_message_text(f"🎲 <b>Кости</b>  {name}\nСтавка: <b>{bet} FC</b>\nВыбери число:",cid,call.message.message_id,reply_markup=kb_dice(bet))
    elif game=="rl":
        bot.edit_message_text(f"🎡 <b>Рулетка</b>  {name}\nСтавка: <b>{bet} FC</b>\nВыбери цвет:",cid,call.message.message_id,reply_markup=kb_roulette(bet))
    elif game=="hl":
        bot.edit_message_text(f"📈 <b>Больше/Меньше</b>  {name}\nСтавка: <b>{bet} FC</b>",cid,call.message.message_id,reply_markup=kb_hl(bet))
    elif game=="mines":
        bot.edit_message_text(f"💣 <b>Мины</b>  {name}\nСтавка: <b>{bet} FC</b>\nВыбери мины:",cid,call.message.message_id,reply_markup=kb_mines(bet))
    elif game=="duel":
        duel_id=f"{cid}_{uid}"
        active_duels[duel_id]={"initiator_id":uid,"initiator_name":name,"bet":bet,"chat_id":cid}
        bot.edit_message_text(f"⚔️ <b>ДУЭЛЬ!</b>\n\n{name} бросает вызов!\n💰 Ставка: <b>{bet} FC</b> с каждого\n\nКто смелый? 👇",cid,call.message.message_id,reply_markup=kb_duel(duel_id,bet))
    elif game=="slots":
        if not try_bet(uid,bet,cid): bot.answer_callback_query(call.id); return
        win,row,mul,prize=play_slots(bet)
        if win: award(uid,prize); text=f"🎰 <b>СЛОТЫ</b>  {name}\n\n╔══ {row} ══╗\n\n🎉 <b>ВЫИГРЫШ ×{mul}</b>\n💰 {bet} → <b>+{prize} FC</b>\n{bal_line(uid)}"
        else: text=f"🎰 <b>СЛОТЫ</b>  {name}\n\n╔══ {row} ══╗\n\n😔 Не повезло — <b>-{bet} FC</b>\n{bal_line(uid)}"
        bot.edit_message_text(text,cid,call.message.message_id)
    elif game=="bj":
        if not try_bet(uid,bet,cid): bot.answer_callback_query(call.id); return
        result,p,d,reason=play_bj()
        if result is True:
            prize=bet*2; award(uid,prize)
            text=f"🃏 <b>Блэкджек</b>  {name}\n\nТы: <b>{p}</b>  │  Дилер: <b>{d}</b>\n✅ {reason}\n\n🎉 <b>ПОБЕДА!</b>  {bet} → <b>+{prize} FC</b>\n{bal_line(uid)}"
        elif result is None:
            award(uid,bet); text=f"🃏 <b>Блэкджек</b>  {name}\n\nТы: <b>{p}</b>  │  Дилер: <b>{d}</b>\n🤝 Ничья — возврат <b>{bet} FC</b>\n{bal_line(uid)}"
        else: text=f"🃏 <b>Блэкджек</b>  {name}\n\nТы: <b>{p}</b>  │  Дилер: <b>{d}</b>\n❌ {reason}\n\n😔 Проигрыш — <b>-{bet} FC</b>\n{bal_line(uid)}"
        bot.edit_message_text(text,cid,call.message.message_id)
    bot.answer_callback_query(call.id)

# ── Монетка ────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("flip:") and c.data.count(":")==2)
def cb_flip(call):
    parts=call.data.split(":"); choice=parts[1]; bet=int(parts[2])
    user=call.from_user; uid=user.id; cid=call.message.chat.id
    if not try_bet(uid,bet,cid): bot.answer_callback_query(call.id); return
    win,result=play_flip(choice)
    chosen="🦅 Орёл" if choice=="орёл" else "🪙 Решка"
    if win: prize=bet*2; award(uid,prize); out=f"🎉 <b>ВЫИГРЫШ!</b>  {bet} → <b>+{prize} FC</b>\n{bal_line(uid)}"
    else: out=f"😔 <b>Проигрыш</b> — <b>-{bet} FC</b>\n{bal_line(uid)}"
    bot.edit_message_text(f"🪙 <b>Монетка</b>  {mention(user)}\n\nВыбор: {chosen}\nРезультат: <b>{result}</b>\n\n{out}",cid,call.message.message_id)
    bot.answer_callback_query(call.id)

# ── Кости ──────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("dice:") and c.data.count(":")==2)
def cb_dice(call):
    parts=call.data.split(":"); guess=int(parts[1]); bet=int(parts[2])
    user=call.from_user; uid=user.id; cid=call.message.chat.id
    if not try_bet(uid,bet,cid): bot.answer_callback_query(call.id); return
    win,emoji,rolled=play_dice(guess)
    if win: prize=bet*5; award(uid,prize); out=f"🎉 <b>УГАДАЛ! ×5</b>  {bet} → <b>+{prize} FC</b>\n{bal_line(uid)}"
    else: out=f"😔 Выбрал <b>{guess}</b>, выпало <b>{rolled}</b> — <b>-{bet} FC</b>\n{bal_line(uid)}"
    bot.edit_message_text(f"🎲 <b>Кости</b>  {mention(user)}\n\nВыбор: <b>{guess}</b>\nРезультат: {emoji} <b>{rolled}</b>\n\n{out}",cid,call.message.message_id)
    bot.answer_callback_query(call.id)

# ── Рулетка ────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("rl:") and c.data.count(":")==2)
def cb_roulette(call):
    parts=call.data.split(":"); color=parts[1]; bet=int(parts[2])
    user=call.from_user; uid=user.id; cid=call.message.chat.id
    if not try_bet(uid,bet,cid): bot.answer_callback_query(call.id); return
    win,emoji_n,mul=play_roulette(color)
    CD={"красное":"🔴 Красное","чёрное":"⚫ Чёрное","зелёное":"🟢 Зелёное"}
    if win: prize=int(bet*mul); award(uid,prize); out=f"🎉 <b>ПОБЕДА! ×{mul}</b>  {bet} → <b>+{prize} FC</b>\n{bal_line(uid)}"
    else: out=f"😔 <b>Проигрыш</b> — <b>-{bet} FC</b>\n{bal_line(uid)}"
    bot.edit_message_text(f"🎡 <b>Рулетка</b>  {mention(user)}\n\nСтавка: {CD[color]}\nРезультат: <b>{emoji_n}</b>\n\n{out}",cid,call.message.message_id)
    bot.answer_callback_query(call.id)

# ── Больше/Меньше ──────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("hl:") and c.data.count(":")==2)
def cb_hl(call):
    parts=call.data.split(":"); choice=parts[1]; bet=int(parts[2])
    user=call.from_user; uid=user.id; cid=call.message.chat.id
    if not try_bet(uid,bet,cid): bot.answer_callback_query(call.id); return
    win,info=play_hl(choice)
    chosen="📈 Больше 50" if choice=="больше" else "📉 Меньше 50"
    if win: prize=bet*2; award(uid,prize); out=f"🎉 <b>УГАДАЛ! ×2</b>  {bet} → <b>+{prize} FC</b>\n{bal_line(uid)}"
    else: out=f"😔 <b>Не угадал</b> — <b>-{bet} FC</b>\n{bal_line(uid)}"
    bot.edit_message_text(f"📈 <b>Больше/Меньше</b>  {mention(user)}\n\nВыбор: {chosen}\nЧисло: <b>{info}</b>\n\n{out}",cid,call.message.message_id)
    bot.answer_callback_query(call.id)

# ── Мины ───────────────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("mines:") and c.data.count(":")==2)
def cb_mines(call):
    parts=call.data.split(":"); mines=int(parts[1]); bet=int(parts[2])
    user=call.from_user; uid=user.id; cid=call.message.chat.id
    if not try_bet(uid,bet,cid): bot.answer_callback_query(call.id); return
    win,cells,mul,prize=play_mines(mines,bet)
    if win:
        award(uid,prize)
        out=f"🎉 <b>ВЫЖИЛ!</b>  Ячеек: <b>{cells}</b>  Множитель: <b>×{mul}</b>\n{bet} → <b>+{prize} FC</b>\n{bal_line(uid)}"
    else:
        out=f"💥 <b>ВЗРЫВ!</b>  Дошёл до <b>{cells}</b> ячеек\n<b>-{bet} FC</b>\n{bal_line(uid)}"
    bot.edit_message_text(f"💣 <b>Мины</b>  {mention(user)}\n\nМин: <b>{mines}</b>  │  Ставка: <b>{bet} FC</b>\n\n{out}",cid,call.message.message_id)
    bot.answer_callback_query(call.id)

# ── Дуэль принять ──────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("da:"))
def cb_duel_accept(call):
    duel_id=call.data[3:]; opponent=call.from_user; cid=call.message.chat.id
    if duel_id not in active_duels:
        bot.answer_callback_query(call.id,"❌ Дуэль уже недействительна!",show_alert=True); return
    duel=active_duels[duel_id]
    if opponent.id==duel["initiator_id"]:
        bot.answer_callback_query(call.id,"❌ Нельзя принять свой вызов!",show_alert=True); return
    bet=duel["bet"]
    iu=get_user(duel["initiator_id"]); ou=get_user(opponent.id,opponent.first_name)
    if iu["balance"]<bet:
        bot.answer_callback_query(call.id,"❌ У инициатора недостаточно FC!",show_alert=True)
        del active_duels[duel_id]; return
    if ou["balance"]<bet:
        bot.answer_callback_query(call.id,f"❌ У тебя недостаточно FC! Нужно {bet} FC",show_alert=True); return
    del active_duels[duel_id]
    charge(duel["initiator_id"],bet); charge(opponent.id,bet)
    if random.randint(0,1)==0: wid,wname=duel["initiator_id"],duel["initiator_name"]
    else: wid,wname=opponent.id,mention(opponent)
    prize=bet*2; award(wid,prize)
    bot.edit_message_text(
        f"⚔️ <b>ДУЭЛЬ ЗАВЕРШЕНА!</b>\n\n"
        f"{duel['initiator_name']}  vs  {mention(opponent)}\n"
        f"💰 Ставка: <b>{bet} FC</b> каждый\n\n"
        f"🏆 <b>ПОБЕДИТЕЛЬ:</b> {wname}\n"
        f"💎 Выигрыш: <b>{prize} FC</b>\n"
        f"💼 Баланс победителя: <b>{users[wid]['balance']} FC</b>",
        cid,call.message.message_id)
    bot.answer_callback_query(call.id,"⚔️ Принято!")

# ── Дуэль отклонить ────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("dd:"))
def cb_duel_decline(call):
    duel_id=call.data[3:]
    if duel_id in active_duels: del active_duels[duel_id]
    bot.edit_message_text("❌ <b>Дуэль отклонена.</b>",call.message.chat.id,call.message.message_id)
    bot.answer_callback_query(call.id)

# ════════════════════════════════════════════
#   ЗАПУСК
# ════════════════════════════════════════════
if __name__=="__main__":
    # Запуск поллера истории в фоне
    t = threading.Thread(target=history_poller, daemon=True)
    t.start()
    log.info("🦊 Game FOX Casino v4 запускается...")
    log.info(f"🔄 Проверка пополнений каждые {POLL_INTERVAL} сек через /service/history")
    bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)

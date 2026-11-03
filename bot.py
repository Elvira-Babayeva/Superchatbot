"""SuperChat core: hybrid rule-based router + Flights / Shopping / Cinema modules.
Fully offline (no LLM). Supports English, Azerbaijani and Russian."""
import logging, os, re, time
from datetime import date, timedelta
from repos import ProviderError
from config import build_flights, build_products, build_showtimes, all_mock

log = logging.getLogger("superchat")
# Repositories are chosen by environment variables (see config.py): mock JSON by default, real APIs when configured.
FR, PR, SR = build_flights(), build_products(), build_showtimes()
_t = os.environ.get("SUPERCHAT_TODAY")
TODAY = date.fromisoformat(_t) if _t else (date(2026, 11, 2) if all_mock() else date.today())  # mock dataset is anchored on Mon 2 Nov 2026

# ---------------------------------------------------------------- text utils
_TR = str.maketrans({"ə": "e", "ı": "i", "ö": "o", "ü": "u", "ş": "s", "ç": "c", "ğ": "g", "ё": "е"})
def norm(s):
    s = s.lower().replace("\u0307", "").replace("sci-fi", "scifi").replace("sci fi", "scifi")
    return s.translate(_TR)
def toks(n): return re.findall(r"[a-z\u0430-\u044f0-9]+", n)
def has(tk, stems): return any(t.startswith(s) for t in tk for s in stems)
def money(x): return f"{round(x, 2):g}"

# ---------------------------------------------------------------- i18n
TX = {
 "ask_domain": ("I can help with flights, shopping or cinema. Which one do you mean?",
                "Uçuşlar, alış-veriş və ya kino üzrə kömək edə bilərəm. Hansını nəzərdə tutursunuz?",
                "Я помогу с авиарейсами, покупками или кино. Что именно вас интересует?"),
 "ask_book": ("What would you like to book: which film and showtime, or which flight?",
              "Nəyi rezerv etmək istəyirsiniz: hansı film və seans, yoxsa hansı uçuş?",
              "Что вы хотите забронировать: какой фильм и сеанс или какой рейс?"),
 "oos": ("Sorry, I can only help with flights, shopping (products and cart) and cinema (films, showtimes, tickets).",
         "Üzr istəyirəm, yalnız uçuşlar, alış-veriş (məhsullar və səbət) və kino (filmlər, seanslar, biletlər) üzrə kömək edə bilirəm.",
         "Извините, я могу помочь только с авиарейсами, покупками (товары и корзина) и кино (фильмы, сеансы, билеты)."),
 "err": ("Something went wrong, please try again.", "Xəta baş verdi, zəhmət olmasa yenidən cəhd edin.",
         "Произошла ошибка, попробуйте ещё раз."),
 "pick": ("Which one do you mean? (e.g. \"the second one\")", "Hansını nəzərdə tutursunuz? (məs. \"ikinci\")",
          "Какой именно? (например, «второй»)"),
 "fl_ask": ("Where would you like to fly, and on what date?", "Hara və hansı tarixdə uçmaq istəyirsiniz?",
            "Куда и в какую дату вы хотите полететь?"),
 "fl_none": ("No flights found for: {q}.", "Bu sorğu üzrə uçuş tapılmadı: {q}.", "Рейсы по запросу не найдены: {q}."),
 "dates": ("Available dates for this route: {d}.", "Bu istiqamət üçün mövcud tarixlər: {d}.",
           "Доступные даты для этого направления: {d}."),
 "assume": ("(Departure from Baku assumed.)", "(Yola düşmə yeri Bakı qəbul edildi.)", "(Вылет из Баку принят по умолчанию.)"),
 "found": ("Here is what I found:", "Tapdıqlarım:", "Вот что нашлось:"),
 "direct": ("direct", "düz reys", "прямой"),
 "stops": ("{n} stop via {via}", "{n} dayanacaq ({via})", "пересадок: {n} ({via})"),
 "yes_dir": ("Yes, it is a direct flight.", "Bəli, düz reysdir.", "Да, это прямой рейс."),
 "no_dir": ("No, it has {n} stop via {via}.", "Xeyr, {n} dayanacaq var ({via}).", "Нет, есть пересадок: {n} ({via})."),
 "seats": ("{n} seats left", "{n} yer qalıb", "осталось мест: {n}"),
 "sold": ("sold out", "yer qalmayıb", "всё продано"),
 "bag": ("{n} kg baggage", "{n} kq baqaj", "багаж {n} кг"),
 "few": ("Only {n} seats available.", "Yalnız {n} yer mövcuddur.", "Доступно мест: {n}."),
 "res_fl": ("Reserved (mock): {d} for {n} passenger(s). Code: {c}", "Rezerv edildi (mock): {d}, sərnişin: {n}. Kod: {c}",
            "Забронировано (mock): {d}, пассажиров: {n}. Код: {c}"),
 "res_ci": ("Reserved (mock): {n} seat(s) for {d}. Total {p} AZN. Code: {c}", "Rezerv edildi (mock): {d} üçün {n} yer. Cəmi {p} AZN. Kod: {c}",
            "Забронировано (mock): {n} мест на {d}. Итого {p} AZN. Код: {c}"),
 "cmp": ("Comparison:", "Müqayisə:", "Сравнение:"),
 "cmp_fl": ("Cheapest: {a}; fastest: {b}.", "Ən ucuz: {a}; ən sürətli: {b}.", "Дешевле всего: {a}; быстрее всего: {b}."),
 "cmp_sh": ("Cheapest: {a}; best rated: {b}.", "Ən ucuz: {a}; ən yüksək reytinq: {b}.", "Дешевле всего: {a}; лучший рейтинг: {b}."),
 "sh_none": ("No matching products found.", "Uyğun məhsul tapılmadı.", "Подходящих товаров не найдено."),
 "in_st": ("in stock: {n}", "stokda: {n}", "в наличии: {n}"),
 "out_st": ("out of stock", "stokda yoxdur", "нет в наличии"),
 "added": ("Added to cart: {x}.", "Səbətə əlavə edildi: {x}.", "Добавлено в корзину: {x}."),
 "removed": ("Removed from cart: {x}.", "Səbətdən silindi: {x}.", "Удалено из корзины: {x}."),
 "oos_item": ("{x} is out of stock.", "{x} stokda yoxdur.", "{x}: нет в наличии."),
 "lim": ("Only {n} in stock.", "Stokda yalnız {n} ədəd var.", "В наличии только {n} шт."),
 "not_cart": ("That item is not in your cart.", "Bu məhsul səbətdə yoxdur.", "Этого товара нет в корзине."),
 "cart_empty": ("Your cart is empty.", "Səbət boşdur.", "Корзина пуста."),
 "cart": ("Cart:", "Səbət:", "Корзина:"),
 "total": ("Total: {t} AZN", "Cəmi: {t} AZN", "Итого: {t} AZN"),
 "ci_none": ("No matching films or showtimes found.", "Uyğun film və ya seans tapılmadı.", "Подходящих фильмов или сеансов не найдено."),
 "ci_dates": ("Showtimes for this selection are on: {d}.", "Bu seçim üçün seanslar: {d}.", "Сеансы по этому запросу: {d}."),
 "ci_list": ("Cinemas:", "Kinoteatrlar:", "Кинотеатры:"),
 "svc_down": ("The data service is temporarily unavailable. Please try again in a moment.",
             "Məlumat xidməti müvəqqəti əlçatmazdır. Bir azdan yenidən cəhd edin.",
             "Сервис данных временно недоступен. Попробуйте чуть позже."),
 "book_link": ("Booking is completed on the partner site: {u}", "Rezervasiya tərəfdaş saytında tamamlanır: {u}",
               "Бронирование завершается на сайте партнёра: {u}"),
 "book_nolink": ("Online booking isn't available through this assistant for this provider.",
                 "Bu provayder üçün onlayn rezervasiya bu köməkçi vasitəsilə mövcud deyil.",
                 "Для этого провайдера онлайн-бронирование через ассистента недоступно."),
 "cached": ("Prices come from cached Aviasales data and may change; seats and baggage are not shown.",
            "Qiymətlər Aviasales keşindən götürülüb və dəyişə bilər; yer və baqaj məlumatı göstərilmir.",
            "Цены взяты из кэша Aviasales и могут измениться; места и багаж не указаны."),
 "vs_found": ("I looked at your photo ({c}). Closest matches in the catalogue:", "Şəklinizə baxdım ({c}). Kataloqda ən yaxın nəticələr:",
              "Я посмотрел ваше фото ({c}). Ближайшие совпадения в каталоге:"),
 "vs_color": ("I can only match by colour here ({c}), so these are colour matches, not object matches:",
              "Burada yalnız rəngə görə axtara bilirəm ({c}), buna görə nəticələr rəng üzrədir, əşya üzrə yox:",
              "Здесь я могу искать только по цвету ({c}), поэтому это совпадения по цвету, а не по предмету:"),
 "vs_none": ("I couldn't find anything similar to your photo ({c}).", "Şəklinizə oxşar heç nə tapılmadı ({c}).",
             "Ничего похожего на ваше фото не найдено ({c})."),
 "vs_bad": ("I couldn't read that image. Please send a JPG, PNG or WebP under 5 MB.",
            "Şəkli oxuya bilmədim. JPG, PNG və ya WebP formatında, 5 MB-dan kiçik göndərin.",
            "Не удалось прочитать изображение. Отправьте JPG, PNG или WebP до 5 МБ."),
 "vs_conf": ("I looked at your photo: {x} ({p}% confident). Closest matches in the catalogue:",
             "Şəklinizə baxdım: {x} ({p}% əminlik). Kataloqda ən yaxın nəticələr:",
             "Я посмотрел ваше фото: {x} (уверенность {p}%). Ближайшие совпадения в каталоге:"),
 "vs_confirm": ("I think the photo shows: {x} ({p}% sure), so I searched for that. Is that right? If not, tell me what it is.",
                "Məncə şəkildə: {x} ({p}% əminlik), buna görə onu axtardım. Bu düzdürmü? Deyilsə, nə olduğunu yazın.",
                "Похоже, на фото: {x} (уверенность {p}%), поэтому я искал это. Верно? Если нет, напишите, что это."),
 "vs_which": ("Your photo could show {a} ({pa}%) or {b} ({pb}%). Which one do you mean?",
              "Şəkildə {a} ({pa}%) və ya {b} ({pb}%) ola bilər. Hansını nəzərdə tutursunuz?",
              "На фото может быть {a} ({pa}%) или {b} ({pb}%). Что именно вы имеете в виду?"),
 "vs_open": ("I'm not sure what is in the photo (best guess: {x}, {p}%). What are you looking for? Type it, e.g. \"headphones\".",
             "Şəkildə nə olduğuna əmin deyiləm (ehtimal: {x}, {p}%). Nə axtarırsınız? Yazın, məs. \"qulaqlıq\".",
             "Я не уверен, что на фото (лучшее предположение: {x}, {p}%). Что вы ищете? Напишите, например, «наушники»."),
 "vs_blank": ("I couldn't recognise anything searchable in this photo. What are you looking for?",
              "Şəkildə axtara biləcəyim bir şey tanıya bilmədim. Nə axtarırsınız?",
              "Я не смог распознать на фото ничего, что можно искать. Что вы ищете?"),
 "vs_ok": ("Great! You can say e.g. \"add the first one to the cart\".", "Əla! Məs. deyin: \"birincini səbətə at\".",
           "Отлично! Скажите, например: «добавь первый в корзину»."),
 "vs_what": ("Okay, what is it? Tell me in a few words.", "Yaxşı, bu nədir? Bir neçə sözlə yazın.", "Хорошо, что это? Напишите в нескольких словах."),
 "vs_yes": ("Yes", "Bəli", "Да"),
 "vs_no": ("No, something else", "Xeyr, başqa", "Нет, другое"),
 "rate": ("Too many photo searches in a short time. Please wait a minute.", "Qısa müddətdə çox şəkil axtarışı olub. Bir dəqiqə gözləyin.",
          "Слишком много поиска по фото за короткое время. Подождите минуту."),
 "welcome": ("Hi! I can help with flights, products and cinema tickets. Try: \"Flights to Istanbul on Friday\".",
             "Salam! Uçuşlar, məhsullar və kino biletləri ilə kömək edə bilərəm. Məs.: \"Cümə günü İstanbula uçuş\".",
             "Привет! Помогу с авиарейсами, товарами и билетами в кино. Например: «Рейсы в Стамбул в пятницу»."),
}
LI = {"en": 0, "az": 1, "ru": 2}
def t(S, key, **kw): return TX[key][LI[S.lang or "en"]].format(**kw)

AZ_W = {"mene", "goster", "ucus", "ucun", "bilet", "ve", "bu", "axsam", "hansi", "qiymet", "varmi", "var", "yoxdur", "sebet",
        "sabah", "bazar", "cume", "senbe", "noyabr", "qeder", "altinda", "ucuz", "iki", "bir", "lazimdir", "isteyirem",
        "axtar", "tap", "olan", "salam", "seans", "elave", "gunu", "gune", "indi", "ancaq", "yer"}
EN_W = {"the", "a", "an", "show", "me", "find", "do", "you", "have", "what", "is", "are", "any", "i", "need", "for", "to",
        "from", "on", "and", "book", "seats", "cart", "add", "under", "please", "hello", "hi", "back", "it", "one"}
EN_STRONG = {"the", "show", "under", "flights", "flight", "please", "what", "book", "cart", "films", "movies", "laptops", "find"}
def detect_lang(raw, n, tk, prev):
    if re.search("[а-яё]", raw.lower()): return "ru"
    if re.search("[əıöüşçğ]", raw.lower()) or any(w in AZ_W for w in tk): return "az"
    if sum(w in EN_W for w in tk) >= 2 or any(w in EN_STRONG for w in tk): return "en"
    return prev or "en"

# ---------------------------------------------------------------- lexicons (normalised, prefix match)
FL = [("flight", 2), ("fly", 2), ("airline", 2), ("airport", 2), ("baggage", 1.5), ("luggage", 1.5), ("nonstop", 1.5),
      ("layover", 1.5), ("departure", 1.5), ("direct", 1), ("stops", 1), ("ucus", 2), ("aviabilet", 3), ("reys", 2),
      ("samolyot", 2), ("baqaj", 1.5), ("tranzit", 1.5), ("рейс", 2), ("авиа", 3), ("самолет", 2), ("перел", 2),
      ("багаж", 1.5), ("пересад", 1.5), ("прямой", 1)]
CI = [("film", 2), ("movie", 2), ("cinema", 2), ("kino", 2), ("showtime", 2), ("screening", 2), ("playing", 2), ("seans", 2),
      ("фильм", 2), ("кино", 2), ("сеанс", 2), ("genre", 1.5), ("janr", 1.5), ("жанр", 1.5), ("oynay", 2), ("gosteril", 2),
      ("идет", 1.5), ("ticket", 1), ("bilet", 1), ("билет", 1), ("seat", 1), ("мест", 1)]
SH = [("cart", 2), ("basket", 2), ("buy", 2), ("purchase", 2), ("stock", 1.5), ("product", 2), ("brand", 1), ("shop", 1.5),
      ("sebet", 2), ("stok", 1.5), ("mehsul", 2), ("almaq", 2), ("korzin", 2), ("куп", 2), ("товар", 2), ("наличи", 1.5),
      ("корзин", 2), ("магазин", 1), ("price", .5), ("qiymet", .5), ("цена", .5), ("цены", .5)]
OOS = ["weather", "hava", "pogoda", "погод", "joke", "recipe", "news", "xeber", "новост", "football", "stock market",
       "president", "translate", "capital of", "who is", "poem"]
BOOK = ["book", "reserve", "rezerv", "bron", "бронир", "заброн", "бронь"]
COMPARE = ["compare", "comparison", "muqayise", "сравн", "versus", "vs"]
BACK = ["back to", "go back", "return to", "geri qayit", "вернемся", "вернуться", "вернёмся"]
GENRES = {"scifi": "sci-fi", "fantas": "sci-fi", "фантаст": "sci-fi", "comed": "comedy", "komed": "comedy", "комед": "comedy",
          "horror": "horror", "qorxu": "horror", "dehset": "horror", "ужас": "horror", "drama": "drama", "драм": "drama",
          "action": "action", "aksiyon": "action", "боевик": "action", "thriller": "thriller", "triller": "thriller",
          "триллер": "thriller", "animat": "animation", "мульт": "animation", "family": "family", "aile": "family",
          "семейн": "family", "romanc": "romance", "romant": "romance", "мелодрам": "romance", "adventure": "adventure",
          "macera": "adventure", "приключ": "adventure", "crime": "crime", "kriminal": "crime", "криминал": "crime",
          "mystery": "mystery", "детектив": "mystery", "sirr": "mystery"}
LANGS = {"english": "English", "ingilis": "English", "английск": "English", "russian": "Russian", "rusca": "Russian",
         "русск": "Russian", "azerbaijani": "Azerbaijani", "azerbaycan": "Azerbaijani", "азербайджан": "Azerbaijani"}
CITIES = {"GYD": ["baku", "baki", "баку"], "IST": ["istanbul", "стамбул"], "DXB": ["dubai", "dubay", "дуба"],
          "SVO": ["moscow", "moskva", "москв"], "TBS": ["tbilisi", "тбилис"], "TAS": ["tashkent", "tashkend", "ташкент"]}
CITY_NAME = {"GYD": "Baku", "IST": "Istanbul", "DXB": "Dubai", "SVO": "Moscow", "TBS": "Tbilisi", "TAS": "Tashkent"}
COLORS = {"black": "black", "qara": "black", "черн": "black", "white": "white", "ag": "white", "бел": "white",
          "blue": "blue", "mavi": "blue", "син": "blue", "голуб": "blue", "silver": "silver", "gumus": "silver",
          "серебр": "silver", "grey": "grey", "gray": "grey", "boz": "grey", "сер": "grey"}
SYN = {"telefon": "smartfon", "телефон": "смартфон", "notebook": "laptop", "headset": "headphones", "ноутбуки": "ноутбук"}
STOP = set("a an the me you do does have has any some show find want need looking for i of in on under below over above with "
           "and or is it are there what which please can get give list cheap cheapest expensive azn stock available add cart "
           "remove compare back to from up max less than more at my your best good new one ones".split())
ORD = [("first", 0), ("1st", 0), ("birinci", 0), ("ilk", 0), ("перв", 0), ("second", 1), ("2nd", 1), ("ikinci", 1),
       ("втор", 1), ("third", 2), ("3rd", 2), ("ucuncu", 2), ("трет", 2)]
NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "bir": 1, "iki": 2, "uc": 3, "dord": 4, "bes": 5,
        "один": 1, "одно": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5}

# ---------------------------------------------------------------- dates / numbers
WD = [("bazar ertesi", 0), ("cersenbe axsami", 1), ("cume axsami", 3), ("cersenbe", 2), ("cume", 4), ("senbe", 5), ("bazar", 6),
      ("monday", 0), ("tuesday", 1), ("wednesday", 2), ("thursday", 3), ("friday", 4), ("saturday", 5), ("sunday", 6),
      ("понедельник", 0), ("вторник", 1), ("сред", 2), ("четверг", 3), ("пятниц", 4), ("суббот", 5), ("воскресень", 6)]
MW = r"(?:jan|yan|feb|fev|mar|apr|may|iyun|jun|iyul|jul|aug|avq|sep|sen|oct|okt|nov|noy|dec|dek|январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)[a-z\u0430-\u044f]*"
MONTH_PREFIX = [("jan", 1), ("yan", 1), ("январ", 1), ("feb", 2), ("fev", 2), ("феврал", 2), ("mar", 3), ("март", 3), ("apr", 4),
                ("апрел", 4), ("may", 5), ("май", 5), ("мая", 5), ("iyun", 6), ("jun", 6), ("июн", 6), ("iyul", 7), ("jul", 7),
                ("июл", 7), ("aug", 8), ("avq", 8), ("август", 8), ("sep", 9), ("sen", 9), ("сентябр", 9), ("oct", 10),
                ("okt", 10), ("октябр", 10), ("nov", 11), ("noy", 11), ("ноябр", 11), ("dec", 12), ("dek", 12), ("декабр", 12)]
def month_of(w): return next(m for p, m in MONTH_PREFIX if w.startswith(p))
def mk_date(month, day):
    """Next occurrence of day/month on or after TODAY (None if the date does not exist)."""
    for y in (TODAY.year, TODAY.year + 1):
        try: d = date(y, month, day)
        except ValueError: return None
        if d >= TODAY: return d
    return None

def parse_date(n):
    """-> (date|None, tonight_flag)"""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", n)
    if m: return date(int(m[1]), int(m[2]), int(m[3])), False
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*-?\s*(" + MW + r")", n)
    if m: return mk_date(month_of(m[2]), int(m[1])), False
    m = re.search(r"(" + MW + r")\s*(\d{1,2})\b", n)
    if m: return mk_date(month_of(m[1]), int(m[2])), False
    m = re.search(r"\b(\d{1,2})[./](0[1-9]|1[0-2])(?!\d)(?!\s*(?:azn|usd|eur|rub|manat))", n)
    if m: return mk_date(int(m[2]), int(m[1])), False
    for w, d in WD:
        if w in n: return TODAY + timedelta(days=(d - TODAY.weekday()) % 7), False
    if re.search(r"tomorrow|\bsabah\b|завтра", n): return TODAY + timedelta(days=1), False
    if re.search(r"tonight|axsam|вечер", n): return TODAY, True
    if re.search(r"today|bugun|сегодня", n): return TODAY, False
    return None, False

def numbers(n):
    return [float(x.replace(",", ".")) for x in re.findall(r"\b(\d+(?:[.,]\d+)?)\b(?!\s*(?:" + MW + r"|st\b|nd\b|rd\b|th\b))", n)]
def price_filter(n):
    nums = numbers(n)
    if not nums: return None, None
    if re.search(r"under|below|less|up ?to|max|qeder|altinda|до |дешевле|менее|не более", n): return None, nums[0]
    if re.search(r"over|above|more than|yuxari|ustunde|дороже|от ", n): return nums[0], None
    return None, None
def count(n, tk):
    m = re.search(r"\b([1-9]\d?)\b(?!\s*(?:" + MW + r"))", n)
    if m: return int(m[1])
    return next((NUMW[w] for w in tk if w in NUMW), 1)
def ordinals(tk):
    return [i for t_ in tk if not t_.startswith("вторн") for k, i in ORD if t_.startswith(k)]

def pick(raw, n, tk, items, selected, price="price_azn", idpat=None):
    """Resolve references like 'the second one', 'the cheaper one', 'F002', 'it'."""
    if not items: return [selected] if selected and has(tk, ["it", "that", "this", "onu", "bu", "этот", "его"]) else []
    out = []
    if idpat:
        out += [x for i in re.findall(idpat, raw.upper()) for x in items if x["id"] == i]
    out += [items[i] for i in ordinals(tk) if i < len(items)]
    if not out and re.search(r"\blast\b|sonuncu|последн", n): out = [items[-1]]
    if not out and re.search(r"cheap|ucuz|дешев", n): out = [min(items, key=lambda x: x[price])]
    if not out and re.search(r"expensive|bahali|дорог", n): out = [max(items, key=lambda x: x[price])]
    if not out and selected and has(tk, ["it", "that", "this", "onu", "bu", "этот", "его"]): out = [selected]
    return out

# ---------------------------------------------------------------- session state
class Session:
    def __init__(s):
        s.lang, s.last, s.prefs = None, None, {}
        s.fl = {"q": {}, "res": [], "sel": None}
        s.sh = {"res": [], "sel": None, "cart": {}, "snap": {}}
        s.ci = {"q": {}, "res": [], "kind": None, "sel": None}
        s.vs, s.choices = None, []        # pending photo clarification / quick-reply buttons for the UI

# ---------------------------------------------------------------- scoring / routing
def words(*vals): return {w for v in vals for w in toks(norm(v))}
class _Cache:
    """Stale-tolerant cache for routing vocabularies: refresh every `ttl` s; if the provider is down keep the old value
    and retry in 30 s, so a CinemaPlus outage never breaks a flights or shopping message."""
    def __init__(s, fn, empty, ttl=600): s.fn, s.v, s.ttl, s.t = fn, empty, ttl, None
    def get(s):
        now = time.monotonic()
        if s.t is None or now - s.t >= s.ttl:
            try: s.v, s.t = s.fn(), now
            except ProviderError as e: log.warning("catalogue refresh failed: %s", e); s.t = now - s.ttl + 30
        return s.v

_ALIAS_EXTRA = {"sumgayit": ["sumqayit", "сумгаит"], "28 mall": ["28mall"]}
def _build_cinema():
    mv, cn = {m["id"]: m for m in SR.movies()}, {c["id"]: c for c in SR.cinemas()}
    alias = {}
    for cid, c in cn.items():
        tail = norm(re.sub(r"(?i)^cinema\s*plus\s*", "", c["name"])).strip()
        alias[cid] = [tail, *_ALIAS_EXTRA.get(tail, [])] if tail else []
    return mv, cn, {i: norm(m["title"]) for i, m in mv.items()}, alias
VOCAB = _Cache(lambda: words(*PR.vocabulary()), set())
CINE = _Cache(_build_cinema, ({}, {}, {}, {}))

def tmatch(a, b):
    """0 = no match, 2 = same word/stem, 1 = substring match"""
    if a == b: return 2
    k = min(6, len(a), len(b))
    if k >= 4 and a[:k] == b[:k]: return 2
    return int((len(a) >= 5 and a.rstrip("s") in b) or (len(b) >= 5 and b in a))

def city_mentions(tk):
    out = []
    for i, w in enumerate(tk):
        for code, stems in CITIES.items():
            for s in stems:
                if w.startswith(s):
                    out.append((i, code, w[len(s):], tk[i - 1] if i else ""))
    return out

def score(n, tk):
    sc = {"flights": 0.0, "shopping": 0.0, "cinema": 0.0}
    _, _, TITLES, CINEMAS = CINE.get(); PROD_VOCAB = VOCAB.get()
    for t_ in tk:
        for dom, lex in (("flights", FL), ("cinema", CI), ("shopping", SH)):
            sc[dom] += max([w for s, w in lex if t_.startswith(s)] or [0])
    m_titles = [i for i, tt in TITLES.items() if tt in n]
    n2 = n
    for i in m_titles: n2 = n2.replace(TITLES[i], " ")
    sc["cinema"] += 3 * bool(m_titles)
    sc["cinema"] += 2 * min(2, sum(any(t_.startswith(g) for g in GENRES) for t_ in tk))
    sc["cinema"] += 2 * any(c in n for cs in CINEMAS.values() for c in cs)
    sc["flights"] += min(3, 1.5 * len(city_mentions(toks(n2))))
    sc["shopping"] += min(4, 2 * sum(any(tmatch(t_, w) for w in PROD_VOCAB) and len(t_) >= 4 and t_ not in STOP for t_ in tk))
    return sc

FOLLOW = ["cheap", "ucuz", "дешев", "expensive", "bahali", "дорог", "last", "sonuncu", "последн"]
def is_followup(n, tk):
    return bool(ordinals(tk) or has(tk, ["it", "that", "this", "one", "onu", "bu", "этот", "его", "тот"]) or
                any(f in n for f in FOLLOW) or re.search(r"and for|what about|how about|а на|а в|bəs|bes ", n) or
                parse_date(n)[0])

def segments(n):
    parts = [p for p in re.split(r"\s*(?:,|;|\?|\band\b|\bvə\b|\bve\b|\bи\b|\balso\b|\bplus\b)\s*", n) if p.strip()]
    out = []
    for p in parts:
        sc = score(p, toks(p)); dom = max(sc, key=sc.get)
        dom = dom if sc[dom] >= 1.5 else None
        if dom is None and out: out[-1][1].append(p)
        elif dom is None: out.append([None, [p]])
        elif out and (out[-1][0] in (dom, None)): out[-1][0] = dom; out[-1][1].append(p)
        else: out.append([dom, [p]])
    return [(d, " ".join(ps)) for d, ps in out if d]

def product_probe(tk):
    """Real shop catalogues cannot be listed, so an otherwise unroutable message is checked with one (cached) search."""
    terms = [w for w in tk if len(w) >= 4 and w not in STOP and not w.isdigit() and not re.fullmatch(MW, w)]
    if not terms: return False
    try: return bool(PR.search(terms))
    except ProviderError: return False

def route(S, raw, n, tk):
    """-> ('domains', [(domain, text)...]) | ('ask', key) """
    sc = score(n, tk)
    top = max(sc, key=sc.get)
    cands = [d for d in sc if sc[d] >= 1.5]
    if any(o in n for o in OOS) and sc[top] < 3: return "ask", "oos"
    if not cands:
        if has(tk, BOOK):
            ok = (S.last == "cinema" and S.ci["res"]) or (S.last == "flights" and S.fl["res"])
            return ("domains", [(S.last, raw)]) if ok else ("ask", "ask_book")
        if S.last and is_followup(n, tk): return "domains", [(S.last, raw)]
        if PR.live and product_probe(tk): return "domains", [("shopping", raw)]
        return "ask", "ask_domain"
    if len(cands) >= 2:
        segs = segments(n)
        if len({d for d, _ in segs}) >= 2: return "domains", segs
    best = [d for d in cands if sc[d] == max(sc[c] for c in cands)]
    return "domains", [(S.last if S.last in best else best[0], raw)]

# ---------------------------------------------------------------- Flights
def fdesc(S, f):
    via = f["via"]; via = ", ".join(via) if isinstance(via, list) else (via or "")
    dep, arr, d = f["departure_local"], f["arrival_local"], f["duration_min"]
    stops = t(S, "direct") if f["stops"] == 0 else t(S, "stops", n=f["stops"], via=via)
    extra = [t(S, "bag", n=f["baggage_kg"])] if f.get("baggage_kg") is not None else []
    if f.get("seats_available") is not None: extra.append(t(S, "seats", n=f["seats_available"]))
    return (f"**{f['airline']} {f['flight_number']}** {f['origin']['city']}→{f['destination']['city']}, "
            f"{dep[8:10]}.{dep[5:7]} {dep[11:]}–{arr[11:]}, {d // 60}h{d % 60:02d}m, {stops}, {money(f['price_azn'])} AZN"
            + "".join(", " + x for x in extra))

def parse_flight(n, tk):
    f = {}
    ms = city_mentions(tk)
    for j, (i, code, rest, prev) in enumerate(ms):
        if prev in ("from", "из") or rest in ("dan", "den", "tan", "ten"): f["origin"] = code
        elif prev in ("to", "в", "до", "на") or rest in ("a", "e", "ya", "ye", "na", "ne"): f["dest"] = code
        elif len(ms) >= 2: f["origin" if j == 0 else "dest"] = code
        else: f["dest"] = code
    d, _ = parse_date(n)
    if d: f["date"] = d
    if re.search(r"direct|nonstop|non-stop|\bduz\b|прямой|прямые", n): f["stops"] = 0
    for a in FR.airlines():
        if norm(a) in n or norm(a.split()[0]) in tk: f["airline"] = a
    f["maxp"] = price_filter(n)[1]
    if re.search(r"earliest|erken|ранн|morning", n): f["sort"] = "dep"
    return {k: v for k, v in f.items() if v is not None}

def flights(S, raw):
    n = norm(raw); tk = toks(n); st = S.fl
    found = parse_flight(n, tk)
    refs = pick(raw, n, tk, st["res"], st["sel"], idpat=r"F\d{3}")
    book, comp = has(tk, BOOK), has(tk, COMPARE)
    geo = any(k in found for k in ("origin", "dest", "date"))
    if any(b in n for b in BACK) and not found and st["res"]:
        return t(S, "found") + "\n" + "\n".join(f"{i}. {fdesc(S, f)}" for i, f in enumerate(st["res"], 1))
    if comp and not geo:
        items = refs if len(refs) >= 2 else [r for r in st["res"][:3]]
        if len(items) < 2: return t(S, "pick")
        a = min(items, key=lambda x: x["price_azn"]); b = min(items, key=lambda x: x["duration_min"])
        return (t(S, "cmp") + "\n" + "\n".join(f"- {fdesc(S, f)}" for f in items) + "\n" +
                t(S, "cmp_fl", a=a["flight_number"], b=b["flight_number"]))
    target = refs[0] if refs and not geo else (st["sel"] if book and not geo and not refs else None)
    if target is None and book and not geo and len(st["res"]) == 1: target = st["res"][0]
    if book and not geo:
        if target is None: return t(S, "pick") if st["res"] else t(S, "ask_book")
        k = count(n, tk)
        if not re.search(r"seat|passeng|people|adult|nefer|yer|пассаж|мест", n): k = 1
        st["sel"] = target
        if not FR.supports_reserve:      # search-only provider (Aviasales): hand off to the partner site
            return t(S, "book_link", u=target["booking_url"]) if target.get("booking_url") else t(S, "book_nolink")
        code = FR.reserve(target["id"], k)
        if not code: return t(S, "few", n=target["seats_available"])
        return t(S, "res_fl", d=fdesc(S, target), n=k, c=code)
    if refs and not geo and st["res"]:
        f = refs[0]; st["sel"] = f; pre = ""
        if "direct" in n or "duz" in n or "прям" in n:
            via = f["via"]; via = ", ".join(via) if isinstance(via, list) else (via or "")
            pre = (t(S, "yes_dir") if f["stops"] == 0 else t(S, "no_dir", n=f["stops"], via=via)) + "\n"
        return pre + fdesc(S, f)
    # ---- new / refined search
    for m in re.finditer(r"\b(?:to|from|из|в)\s+([a-z\u0430-\u044f]{3,})", n):
        w = m[1]
        if (not any(w.startswith(c) for cs in CITIES.values() for c in cs) and w not in STOP and parse_date(w)[0] is None
                and not re.match(r"nov|ноябр|airport|аэропорт", w)):
            return t(S, "fl_none", q=w.capitalize())
    q = dict(st["q"]); q.update(found)
    if "origin" in found and "dest" not in found and "dest" in q and q["dest"] == found["origin"]: q.pop("dest")
    note = ""
    if q.get("dest") and not q.get("origin") and q["dest"] != "GYD": q["origin"] = "GYD"; note = t(S, "assume") + "\n"
    if not q: return t(S, "fl_ask")
    st["q"] = q
    res = [f for f in FR.search(q.get("origin"), q.get("dest"), q.get("date")) if
           (q.get("stops") is None or f["stops"] <= q["stops"]) and (not q.get("airline") or f["airline"] == q["airline"]) and
           (not q.get("maxp") or f["price_azn"] <= q["maxp"])]
    res.sort(key=lambda f: f["departure_local"] if q.get("sort") == "dep" else f["price_azn"])
    res = res[:6]
    desc = " ".join(filter(None, [CITY_NAME.get(q.get("origin"), ""), "→" if q.get("origin") and q.get("dest") else "",
                                  CITY_NAME.get(q.get("dest"), ""), q["date"].strftime("%d.%m.%Y") if q.get("date") else ""]))
    st["res"], st["sel"] = res, (res[0] if len(res) == 1 else None)
    if not res:
        other = [d[8:10] + "." + d[5:7] for d in FR.available_dates(q.get("origin"), q.get("dest"))]
        return note + t(S, "fl_none", q=desc.strip()) + (" " + t(S, "dates", d=", ".join(other)) if other and q.get("date") else "")
    cached = ("\n" + t(S, "cached")) if any(f.get("source") == "aviasales" for f in res) else ""
    return note + t(S, "found") + "\n" + "\n".join(f"{i}. {fdesc(S, f)}" for i, f in enumerate(res, 1)) + cached

# ---------------------------------------------------------------- Shopping
def pname(S, p): return p["name"][S.lang or "en"]
def pdesc(S, p):
    stock = t(S, "in_st", n=p["stock"]) if p["stock"] else t(S, "out_st")
    return f"**{pname(S, p)}** ({p['brand']}) — {money(p['price_azn'])} AZN, ⭐{p['rating']}, {p['color']}, {stock}"
def prod(S, i): return S.sh["snap"].get(i) or PR.get(i)   # price/name as they were when added to the cart
def cart_text(S):
    c = S.sh["cart"]
    if not c: return t(S, "cart_empty")
    lines = [f"- {pname(S, prod(S, i))} × {q} = {money(prod(S, i)['price_azn'] * q)} AZN" for i, q in c.items()]
    return t(S, "cart") + "\n" + "\n".join(lines) + "\n" + t(S, "total", t=money(sum(prod(S, i)["price_azn"] * q for i, q in c.items())))

def product_search(tk, n):
    color = next((v for t_ in tk for k, v in COLORS.items() if t_.startswith(k) and (len(k) > 3 or t_ == k)), None)
    minp, maxp = price_filter(n)
    q = [SYN.get(w, w) for w in tk if len(w) >= 3 and w not in STOP and not w.isdigit() and w not in COLORS and
         not any(w.startswith(c) for c in COLORS if len(c) > 3)]
    hits, cand = {}, {}
    for p in (PR.search(q) if q else PR.all()):        # real shops: the API narrows the candidates, we re-rank locally
        cand[p["id"]] = p
        s = sum(max(tmatch(w, v) for v in words(*p["name"].values(), p["brand"], p["category"], p["color"])) for w in q)
        if s: hits[p["id"]] = s
    named = bool(hits)
    if hits:
        best = max(hits.values()); hits = {k: v for k, v in hits.items() if v == best}
    pool = [cand[i] for i in hits] if hits else []
    res = [p for p in pool if (not color or p["color"] == color) and (not minp or p["price_azn"] >= minp) and
           (not maxp or p["price_azn"] <= maxp)]
    return res, named, bool(color or minp or maxp)

def shopping(S, raw):
    n = norm(raw); tk = toks(n); st = S.sh
    refs = pick(raw, n, tk, st["res"], st["sel"], idpat=r"P\d{3}")
    hits, named, filt = product_search(tk, n)
    add = has(tk, ["add", "put", "elave", "добав", "polož", "полож"]); rem = has(tk, ["remove", "delete", "sil", "удал", "убер"])
    if any(b in n for b in BACK) and named and hits: pass
    elif any(b in n for b in BACK) and not named: return (t(S, "found") + "\n" + "\n".join(
        f"{i}. {pdesc(S, p)}" for i, p in enumerate(st["res"], 1)) + "\n" if st["res"] else "") + cart_text(S)
    if add or rem:
        tgt = refs[0] if refs else (hits[0] if len(hits) == 1 else (st["sel"] if not named else None))
        if tgt is None:
            if hits: st["res"] = hits
            return t(S, "pick")
        st["sel"] = tgt; c = st["cart"]
        if rem:
            if tgt["id"] not in c: return t(S, "not_cart")
            del c[tgt["id"]]; return t(S, "removed", x=pname(S, tgt)) + "\n" + cart_text(S)
        k = count(n, tk) if re.search(r"\b[2-9]\b|two|three|iki|два", n) else 1
        if not tgt["stock"]: return t(S, "oos_item", x=pname(S, tgt))
        if c.get(tgt["id"], 0) + k > tgt["stock"]: return t(S, "lim", n=tgt["stock"])
        c[tgt["id"]] = c.get(tgt["id"], 0) + k; st["snap"][tgt["id"]] = tgt
        return t(S, "added", x=pname(S, tgt)) + "\n" + cart_text(S)
    if has(tk, COMPARE):
        items = refs if len(refs) >= 2 else (hits if len(hits) >= 2 else st["res"][:2])
        if len(items) < 2: return t(S, "pick")
        a = min(items, key=lambda p: p["price_azn"]); b = max(items, key=lambda p: p["rating"])
        return (t(S, "cmp") + "\n" + "\n".join(f"- {pdesc(S, p)} — {p['description']}" for p in items) + "\n" +
                t(S, "cmp_sh", a=pname(S, a), b=pname(S, b)))
    if has(tk, ["cart", "basket", "sebet", "корзин"]) and not named: return cart_text(S)
    if refs and not named:
        p = refs[0]; st["sel"] = p
        return pdesc(S, p) + f"\n{p['description']}"
    if named or filt:
        if not named and st["res"]:  # refine the current shortlist
            minp, maxp = price_filter(n)
            hits = [p for p in st["res"] if (not maxp or p["price_azn"] <= maxp) and (not minp or p["price_azn"] >= minp)]
        hits.sort(key=lambda p: p["price_azn"])
        if not hits: return t(S, "sh_none")
        st["res"] = hits; st["sel"] = hits[0] if len(hits) == 1 else None
        if len(hits) == 1: return pdesc(S, hits[0]) + f"\n{hits[0]['description']}"
        return t(S, "found") + "\n" + "\n".join(f"{i}. {pdesc(S, p)}" for i, p in enumerate(hits, 1))
    return t(S, "sh_none")

# ---------------------------------------------------------------- Cinema
def sdesc(S, s):
    MV, CN, _, _ = CINE.get()
    m, c = MV.get(s["movie_id"], {"title": "?"}), CN.get(s["cinema_id"], {"name": s["cinema_id"], "district": ""})
    seats = t(S, "seats", n=s["seats_available"]) if s["seats_available"] else t(S, "sold")
    return (f"**{m['title']}** — {c['name']} ({c['district']}), {s['date'][8:]}.{s['date'][5:7]} {s['time']}, {s['hall']}, "
            f"{money(s['price_azn'])} AZN, {seats}")
def mdesc(m): return f"**{m['title']}** ({', '.join(m['genre'])}, {m['language']}, {m['duration_min']} min, {m['age_rating']}, ⭐{m['rating']})"

def parse_cinema(n, tk):
    f = {}; _, _, TITLES, CINEMAS = CINE.get()
    ids = [i for i, tt in TITLES.items() if tt in n]
    if ids: f["title"] = ids[0]
    g = next((GENRES[k] for t_ in tk for k in GENRES if t_.startswith(k)), None)
    if g: f["genre"] = g
    lg = next((v for t_ in tk for k, v in LANGS.items() if t_.startswith(k)), None)
    if lg: f["lang"] = lg
    m = re.search(r"(?:rating|reyting|рейтинг)\D{0,12}(\d+(?:[.,]\d)?)", n)
    if m: f["minr"] = float(m[1].replace(",", "."))
    d, tonight = parse_date(n)
    if d: f["date"] = d
    if tonight: f["tonight"] = True
    for cid, names in CINEMAS.items():
        if any(x in n for x in names): f["cin"] = cid
    return f

def cinema(S, raw):
    MV, CN, _, _ = CINE.get()
    n = norm(raw); tk = toks(n); st = S.ci
    found = parse_cinema(n, tk)
    refs = pick(raw, n, tk, st["res"], st["sel"] if st["kind"] == "st" else None, idpat=r"S\d{3}")
    book = has(tk, BOOK)
    filt_keys = {"title", "genre", "lang", "minr", "cin"}
    if any(b in n for b in BACK) and not found and st["res"]:
        d = sdesc if st["kind"] == "st" else (lambda S_, m: mdesc(m))
        return t(S, "found") + "\n" + "\n".join(f"{i}. {d(S, x)}" for i, x in enumerate(st["res"], 1))
    if re.search(r"\bcinemas\b|kinoteatrlar|кинотеатры", n) and not (found.keys() & filt_keys) and not book:
        return t(S, "ci_list") + "\n" + "\n".join(f"- {c['name']}, {c['address']}" for c in SR.cinemas())
    if refs and st["kind"] == "movies" and not (found.keys() & {"genre", "lang", "minr", "date", "title"}):
        found = {"title": refs[0]["id"]}; refs = []
    if book:
        s = refs[0] if refs and st["kind"] == "st" else None
        if s is None and not found and st["kind"] == "st":
            s = st["sel"] or (st["res"][0] if len(st["res"]) == 1 else None)
        if s is None and "title" in found:
            c = [x for x in SR.showtimes() if x["movie_id"] == found["title"] and (not found.get("date") or x["date"] == found["date"].isoformat())]
            s = c[0] if len(c) == 1 else None
            if len(c) > 1:
                st["kind"], st["res"], st["sel"] = "st", c, None
                return t(S, "pick") + "\n" + "\n".join(f"{i}. {sdesc(S, x)}" for i, x in enumerate(c, 1))
        if s is None: return t(S, "ask_book") if not st["res"] else t(S, "pick")
        k = count(n, tk); st["sel"] = s
        if not SR.supports_reserve:
            u = SR.checkout_url(s["id"]); return t(S, "book_link", u=u) if u else t(S, "book_nolink")
        if s["seats_available"] < k: return t(S, "few", n=s["seats_available"]) if s["seats_available"] else t(S, "sold")
        code = SR.reserve(s["id"], k)
        return t(S, "res_ci", n=k, d=f"{MV.get(s['movie_id'], {}).get('title', '?')}, {CN.get(s['cinema_id'], {}).get('name', '?')}, {s['date']} {s['time']}",
                 p=money(k * s["price_azn"]), c=code)
    if refs and st["kind"] == "st" and not found:
        st["sel"] = refs[0]; return sdesc(S, refs[0])
    # ---- search (date-only messages keep the previous film filters)
    q = {k: v for k, v in found.items() if k in filt_keys | {"date", "tonight"}}
    if found.keys() <= {"date", "tonight"} and found:
        q = {**{k: v for k, v in st["q"].items() if k in filt_keys}, **q}
    st["q"] = q
    movies = [m for m in SR.movies() if (not q.get("title") or m["id"] == q["title"]) and
              (not q.get("genre") or q["genre"] in m["genre"]) and (not q.get("lang") or m["language"] == q["lang"]) and
              (not q.get("minr") or m["rating"] >= q["minr"])]
    ids = {m["id"] for m in movies}
    base = [s for s in SR.showtimes() if s["movie_id"] in ids and (not q.get("cin") or s["cinema_id"] == q["cin"])]
    sts = [s for s in base if (not q.get("date") or s["date"] == q["date"].isoformat()) and (not q.get("tonight") or s["time"] >= "18:00")]
    sts.sort(key=lambda s: (s["date"], s["time"]))
    show_sts = bool(q.get("date") or q.get("title") or q.get("cin") or has(tk, ["seat", "showtime", "playing", "seans", "мест", "сеанс", "oynay", "gosteril", "идет"]))
    if show_sts:
        if not sts:
            ds = sorted({s["date"][8:] + "." + s["date"][5:7] for s in base})
            return t(S, "ci_none") + (" " + t(S, "ci_dates", d=", ".join(ds)) if ds else "")
        st["kind"], st["res"], st["sel"] = "st", sts, (sts[0] if len(sts) == 1 else None)
        return t(S, "found") + "\n" + "\n".join(f"{i}. {sdesc(S, s)}" for i, s in enumerate(sts, 1))
    if not movies: return t(S, "ci_none")
    movies.sort(key=lambda m: -m["rating"])
    st["kind"], st["res"], st["sel"] = "movies", movies, None
    return t(S, "found") + "\n" + "\n".join(f"{i}. {mdesc(m)}" for i, m in enumerate(movies, 1))

HANDLERS = {"flights": flights, "shopping": shopping, "cinema": cinema}
def run(S, dom, text):
    try: return HANDLERS[dom](S, text)
    except ProviderError as e:       # one failing provider must not break the other domains of a mixed message
        log.warning("%s provider failed: %s", dom, e); return down(S, e)

# ---------------------------------------------------------------- entry point
def respond(S, raw):
    """-> (reply_text, [domains])"""
    try:
        S.choices = []
        n = norm(raw); tk = toks(n)
        S.lang = detect_lang(raw, n, tk, S.lang)
        if S.vs:
            done, raw2 = vs_answer(S, raw, n, tk)
            if done: return done
            if raw2 != raw: raw = raw2; n = norm(raw); tk = toks(n)
        kind, val = route(S, raw, n, tk)
        if kind == "ask": return t(S, val), []
        if len(val) == 1:
            dom, text = val[0]; S.last = dom
            return run(S, dom, text), [dom]
        icons = {"flights": "✈️", "shopping": "🛍️", "cinema": "🎬"}
        parts = [f"{icons[d]} {run(S, d, txt)}" for d, txt in val]
        S.last = val[-1][0]
        return "\n\n".join(parts), [d for d, _ in val]
    except Exception:  # never leak stack traces to the user
        import logging; logging.exception("handler failed")
        return t(S, "err"), []

# ---------------------------------------------------------------- Visual search
DEBUG = os.environ.get("SUPERCHAT_DEBUG", "").lower() in ("1", "true", "yes")
def down(S, e):
    """'service unavailable' message; with SUPERCHAT_DEBUG=1 it also shows the (key-redacted) reason, e.g. 'HTTP 401: invalid x-api-key'."""
    return t(S, "svc_down") + (f"\n[debug] {e}" if DEBUG else "")

def _cap(info): return info.get("caption") or ", ".join(info.get("keywords", []) + info.get("colors", [])) or "?"
def _pct(x): return round(float(x) * 100)

# Photo confidence policy (override with env). >= CONFIDENT: answer; MIN..CONFIDENT: answer but ask to confirm; < MIN: do not search, ask.
VS_CONFIDENT, VS_MIN, VS_AMBIGUOUS_GAP = (float(os.environ.get(k, d)) for k, d in
                                          (("VS_CONFIDENT", "0.75"), ("VS_MIN", "0.40"), ("VS_AMBIGUOUS_GAP", "0.15")))
YES_W = {"yes", "yeah", "yep", "yup", "correct", "right", "ok", "okay", "sure", "bəli", "beli", "he", "duzdur", "dogru", "hə",
         "да", "верно", "точно", "ага", "угу", "конечно", "правильно"}
NO_W = {"no", "nope", "nah", "xeyr", "yox", "нет", "неа", "не"}

def _vs_hits(S, kw, colors):
    q = toks(" ".join(norm(k) for k in kw))
    if not q: return []
    tk = q + [c for c in (norm(c) for c in colors) if c in COLORS][:1]
    hits, _, _ = product_search(tk, " ".join(tk))
    if not hits: hits, _, _ = product_search(q, " ".join(q))       # colour was too strict -> drop it
    hits = sorted(hits, key=lambda p: -p["rating"])[:5]
    if hits: S.sh["res"], S.sh["sel"] = hits, (hits[0] if len(hits) == 1 else None)
    return hits
def _vs_list(S, hits): return "\n".join(f"{i}. {pdesc(S, p)}" for i, p in enumerate(hits, 1))
def _cand_kw(c): return c["keywords"] + ([c["brand"]] if c.get("brand") else [])

def visual_search(S, info):
    """info = VisionProvider.describe() result -> (reply, [domains]).
    1) a readable film title (poster) -> cinema showtimes;
    2) Claude-style result with candidates + confidence -> confident: answer; medium: answer + ask to confirm; ambiguous: ask which;
       low: do not search, ask what it is (the answer is handled by vs_answer on the next message);
    3) colour only (offline provider) -> colour matches, clearly labelled as such."""
    S.vs, S.choices = None, []
    try:
        _, _, TITLES, _ = CINE.get()
        blob = norm(" ".join(info.get("text", [])))
        tid = next((i for i, tt in TITLES.items() if tt and tt in blob), None)
        if tid:
            S.last = "cinema"; return cinema(S, "showtimes " + TITLES[tid]), ["cinema"]
        S.last = "shopping"
        if info.get("provider") != "color" and "candidates" in info:
            return _vs_confidence_flow(S, info)
        kw, colors = info.get("keywords", []), info.get("colors", [])
        if kw:
            hits = _vs_hits(S, kw, colors)
            if hits: return t(S, "vs_found", c=_cap(info)) + "\n" + _vs_list(S, hits), ["shopping"]
            return t(S, "vs_none", c=_cap(info)), ["shopping"]
        hits = [p for p in PR.all() if p["color"] in [norm(c) for c in colors] and p["stock"]]       # colour-only fallback
        if not hits: return t(S, "vs_none", c=_cap(info)), ["shopping"]
        hits.sort(key=lambda p: -p["rating"]); hits = hits[:5]
        S.sh["res"], S.sh["sel"] = hits, None
        return t(S, "vs_color", c=_cap(info)) + "\n" + _vs_list(S, hits), ["shopping"]
    except ProviderError as e:
        log.warning("visual search provider failed: %s", e); return down(S, e), []

def _vs_confidence_flow(S, info):
    cands = info.get("candidates") or []
    if not cands:
        S.vs = {"state": "open", "cands": []}; return t(S, "vs_blank"), ["shopping"]
    top = cands[0]; c = top["confidence"]
    if c < VS_MIN:                                              # guessing: do not show products that may be wrong
        S.vs = {"state": "open", "cands": cands}; S.choices = [x["label"] for x in cands]
        return t(S, "vs_open", x=top["label"], p=_pct(c)), ["shopping"]
    if len(cands) > 1 and cands[1]["confidence"] >= VS_MIN and c - cands[1]["confidence"] < VS_AMBIGUOUS_GAP:
        S.vs = {"state": "which", "cands": cands[:2]}; S.choices = [x["label"] for x in cands[:2]]
        return t(S, "vs_which", a=top["label"], pa=_pct(c), b=cands[1]["label"], pb=_pct(cands[1]["confidence"])), ["shopping"]
    hits = _vs_hits(S, _cand_kw(top), top["colors"])
    if not hits: return t(S, "vs_none", c=top["label"]), ["shopping"]
    body = _vs_list(S, hits)
    if c >= VS_CONFIDENT: return t(S, "vs_conf", x=top["label"], p=_pct(c)) + "\n" + body, ["shopping"]
    S.vs = {"state": "confirm", "cands": cands}; S.choices = [t(S, "vs_yes"), t(S, "vs_no")]
    return body + "\n\n" + t(S, "vs_confirm", x=top["label"], p=_pct(c)), ["shopping"]

def vs_answer(S, raw, n, tk):
    """The user's reply to a photo clarification. -> ((reply, domains) | None, raw_to_route).
    Anything that is not a yes/no/pick is routed as a normal message ('headphones' -> shopping search)."""
    st = S.vs; first = tk[0] if tk else ""
    if len(tk) <= 3 and any(w in YES_W for w in tk) and not any(w in NO_W for w in tk):
        S.vs = None
        if st["state"] == "confirm": return (t(S, "vs_ok"), ["shopping"]), raw
        return None, raw                                         # "yes" to an open question carries no information
    if first in NO_W:
        rest = re.sub(r"^\W*\w+\W*", "", raw, count=1).strip()
        if rest: S.vs = None; return None, rest                  # "no, it's a backpack" -> search the remainder
        S.vs = {"state": "open", "cands": st["cands"]}; S.choices = [x["label"] for x in st["cands"] if x["label"]][:3]
        return (t(S, "vs_what"), ["shopping"]), raw
    cands = st["cands"]
    idx = [i for i in ordinals(tk) if i < len(cands)]
    if st["state"] in ("which", "open") and cands:
        pick_i = idx[0] if idx and st["state"] == "which" else next(
            (i for i, c in enumerate(cands) if any(tmatch(w, v) for w in tk if len(w) >= 3 for v in words(c["label"], *c["keywords"]))), None)
        if pick_i is not None:
            c = cands[pick_i]; S.vs = None
            hits = _vs_hits(S, _cand_kw(c), c["colors"])
            S.last = "shopping"
            return ((t(S, "vs_conf", x=c["label"], p=_pct(c["confidence"])) + "\n" + _vs_list(S, hits), ["shopping"]) if hits
                    else (t(S, "vs_none", c=c["label"]), ["shopping"])), raw
    S.vs = None
    return None, raw

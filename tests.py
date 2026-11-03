"""Routing + behaviour tests (run: python tests.py)."""
import os; os.environ["SUPERCHAT_NO_DOTENV"] = "1"       # a developer .env must not change test results
from bot import Session, respond
CONV = [
 ("1 cross-domain", [("Find me a flight from Baku to Istanbul on Friday.", ["flights"]), ("What is playing tonight?", ["cinema"]),
                     ("Do you have wireless headphones under 100 AZN?", ["shopping"])]),
 ("2 switch+return", [("What is playing tonight?", ["cinema"]), ("Show me laptops.", ["shopping"]),
                      ("Okay, and the sci-fi one, any seats on Saturday?", ["cinema"])]),
 ("3 shopping follow-up", [("Show me laptops.", ["shopping"]), ("The second one, is it in stock?", ["shopping"])]),
 ("4 flights follow-up", [("Flights to Istanbul on 6 November.", ["flights"]), ("The cheaper one, is it direct?", ["flights"])]),
 ("5 mixed", [("I need a flight to Istanbul on 6 November and a backpack for the trip.", ["flights", "shopping"])]),
 ("6 ambiguous", [("Show me something good.", [])]),
 ("7 out of scope", [("What is the weather in Ganja?", [])]),
 ("8 no context booking", [("Book two seats.", [])]),
 ("9 az", [("Bakıdan İstanbula 6 noyabr uçuş göstər", ["flights"]), ("Simsiz qulaqlıq varmı?", ["shopping"]), ("Bu axşam hansı film var?", ["cinema"])]),
 ("10 ru", [("Покажи рейсы Баку Дубай", ["flights"]), ("Ноутбуки есть?", ["shopping"]), ("Какие комедии идут в кино?", ["cinema"])]),
 ("11 cart+return", [("Wireless headphones", ["shopping"]), ("Add it to the cart", ["shopping"]), ("Flights to Dubai", ["flights"]),
                     ("Back to the headphones", ["shopping"]), ("Show my cart", ["shopping"])]),
]
ok = tot = 0
for name, turns in CONV:
    S = Session(); print(f"\n=== {name}")
    for msg, exp in turns:
        r, d = respond(S, msg); tot += 1; ok += d == exp
        print(f"{'PASS' if d == exp else 'FAIL'} [{','.join(d)}] {msg}\n   -> {r[:400]}".replace("\n", "\n   "))
print(f"\nRouting accuracy: {ok}/{tot} = {ok / tot:.0%}")

import requests
from pymongo import MongoClient
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib

mongo = MongoClient("mongodb://localhost:27017/")
db = mongo["dominionabyss"]
cards = db.cards

base_url = "http://localhost:5711"  # or your deployed URL

def get_etag(card):
    # Use the same logic as in app.py for ETag
    card_id = card.get('id') or card.get('_id')
    if card_id:
        return hashlib.md5(str(card_id).encode()).hexdigest()
    return None

def fetch_card(card):
    set_slug = card["set_name"].lower().replace(' ', '-')
    card_slug = card["card_name"].lower().replace(' ', '-')
    url = f"{base_url}/card/{set_slug}/{card_slug}"
    etag = get_etag(card)
    headers = {"If-None-Match": etag} if etag else {}
    try:
        r = requests.head(url, headers=headers, timeout=10)
        if r.status_code == 304:
            return f"SKIP (cached) {url}"
        return f"HEAD {url} -> {r.status_code}"
    except Exception as e:
        return f"Error fetching {url}: {e}"

all_cards = list(cards.find({"card_name": {"$exists": True}, "set_name": {"$exists": True}}))

with ThreadPoolExecutor(max_workers=16) as executor:
    futures = [executor.submit(fetch_card, c) for c in all_cards]
    for future in as_completed(futures):
        print(future.result())
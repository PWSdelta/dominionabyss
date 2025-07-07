from flask import send_from_directory
from flask import request
from flask import Flask, render_template_string, redirect, url_for, render_template, send_from_directory
from functools import lru_cache
import glob
import os
from pymongo import MongoClient
import markdown
import re

app = Flask(__name__)

# MongoDB setup
mongo = MongoClient("mongodb://localhost:27017/")
db = mongo["dominionabyss"]       
cards = db.cards
       
# Convert slug to readable name
def slug_to_name(slug):
    return slug.replace('-', ' ').title()


# Route to view card page (cached for 10 minutes)
from flask import make_response
from functools import lru_cache
import hashlib

def _card_cache_key(set_slug, card_slug):
    # Use slugs as cache key, safe for lru_cache
    return f"{set_slug.lower()}::{card_slug.lower()}"

@lru_cache(maxsize=256)
def get_card_page_cached(set_slug, card_slug):
    card_name = slug_to_name(card_slug)
    set_name = slug_to_name(set_slug)
    card_doc = cards.find_one({
        "card_name": {"$regex": f"^{card_name}$", "$options": "i"},
        "set_name": {"$regex": f"^{set_name}$", "$options": "i"}
    })
    if not card_doc:
        return None, None
    # Format card_text for display: replace newlines with <br>
    if card_doc.get("card_text"):
        # Remove all newlines and replace with a single space, then replace multiple spaces with one
        import re as _re
        card_doc["card_text"] = _re.sub(r'\s+', ' ', card_doc["card_text"]).strip()
    strategy_html = None
    if card_doc.get("strategy_review"):
        linked_md = autolink_card_names(card_doc["strategy_review"], skip_card_name=card_doc.get("card_name"))
        strategy_html = markdown.markdown(linked_md)
    # Render template to string for caching
    html = render_template(
        "card.html",
        card=card_doc,
        strategy_html=strategy_html,
        set_slug=set_slug,
        card_slug=card_slug
    )
    return html, card_doc.get('id') or card_doc.get('_id')

import time
@app.route('/card/<set_slug>/<card_slug>')
def card_page(set_slug, card_slug):
    html, card_id = get_card_page_cached(set_slug, card_slug)
    if html is None:
        print("[ERROR] Card not found!")
        return "Card not found", 404
    # Set cache headers (10 min)
    resp = make_response(html)
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    # ETag for browser cache (optional)
    if card_id:
        etag = hashlib.md5(str(card_id).encode()).hexdigest()
        resp.set_etag(etag)
    return resp

# Caching for homepage queries (cache for 5 minutes)

@lru_cache(maxsize=1)
def get_homepage_data():
    # Build a set of all available image paths (lowercase, hyphenated)
    image_files = glob.glob(os.path.join(app.root_path, 'static', 'card-front-images', '*', '*.jpg'))
    available_images = set()
    for path in image_files:
        parts = path.split(os.sep)
        if len(parts) >= 2:
            set_name = parts[-2].lower()
            card_name = os.path.splitext(parts[-1])[0].lower()
            available_images.add((set_name, card_name))

    def has_image(card):
        set_name = card.get('set_name', '').lower().replace(' ', '-')
        card_name = card.get('card_name', '').lower().replace(' ', '-')
        return (set_name, card_name) in available_images

    # Cache each card grid as a tuple of card IDs (for stability)
    def grid_cache_key(query, limit=8, sort=None):
        # Use a tuple of IDs for the grid
        q = list(cards.find(query).sort(sort) if sort else cards.find(query))
        filtered = [c for c in q if has_image(c)][:limit]
        return tuple(c.get('id') or c.get('_id') for c in filtered)

    featured_set = "Prosperity"
    featured_cards_ids = grid_cache_key({"set_name": {"$regex": f"^{featured_set}$", "$options": "i"}, "card_name": {"$exists": True}})
    five_cost_cards_ids = grid_cache_key({"cost": {"$regex": "\\$5"}, "card_name": {"$exists": True}})
    action_cards_ids = grid_cache_key({"type": {"$regex": "Action", "$options": "i"}, "card_name": {"$exists": True}})
    attack_cards_ids = grid_cache_key({"type": {"$regex": "Attack", "$options": "i"}, "card_name": {"$exists": True}})
    popular_cards_ids = grid_cache_key({"strategy_review": {"$exists": True}}, sort=[("strategy_review", -1)])
    newest_reviews_ids = grid_cache_key({"strategy_review": {"$exists": True}}, sort=[("_id", -1)])
    unanalyzed_cards_ids = grid_cache_key({"card_name": {"$exists": True}, "set_name": {"$exists": True}, "strategy_review": {"$exists": False}})
    beginner_cards_ids = grid_cache_key({"$or": [
        {"card_name": {"$regex": "Silver", "$options": "i"}},
        {"card_name": {"$regex": "Village", "$options": "i"}}
    ], "card_name": {"$exists": True}})

    # Now fetch the actual card docs for each grid (using cached IDs)
    def fetch_cards_by_ids(ids):
        # Maintain order
        return [cards.find_one({"$or": [{"id": i}, {"_id": i}]}) for i in ids if i]

    return {
        "featured_set": featured_set,
        "featured_cards": fetch_cards_by_ids(featured_cards_ids),
        "five_cost_cards": fetch_cards_by_ids(five_cost_cards_ids),
        "action_cards": fetch_cards_by_ids(action_cards_ids),
        "attack_cards": fetch_cards_by_ids(attack_cards_ids),
        "popular_cards": fetch_cards_by_ids(popular_cards_ids),
        "newest_reviews": fetch_cards_by_ids(newest_reviews_ids),
        "unanalyzed_cards": fetch_cards_by_ids(unanalyzed_cards_ids),
        "beginner_cards": fetch_cards_by_ids(beginner_cards_ids)
    }


@app.route('/')
def index():
    data = get_homepage_data()
    resp = make_response(render_template(
        "index.html",
        **data
    ))
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

import random
# Route for a random card
@app.route('/random')
def random_card():
    # Only select cards with a name and set
    card_docs = list(cards.find({"card_name": {"$exists": True}, "set_name": {"$exists": True}}))
    if not card_docs:
        return "No cards found", 404
    card = random.choice(card_docs)
    set_slug = card['set_name'].lower().replace(' ', '-')
    card_slug = card['card_name'].lower().replace(' ', '-')
    return redirect(url_for('card_page', set_slug=set_slug, card_slug=card_slug))

# Serve card images from static/card-front-images/<set>/<card>.jpg
@app.route('/card-front-images/<set_name>/<filename>')
def card_image(set_name, filename):
    # The images are in static/card-front-images/<set_name>/<filename>
    return send_from_directory(
        os.path.join(app.root_path, 'static', 'card-front-images', set_name),
        filename
    )

@app.route('/search')
def search():
    query = (request.args.get('q') or '').strip()
    results = []
    # Build set of all available images (lowercase, hyphenated)
    image_files = glob.glob(os.path.join(app.root_path, 'static', 'card-front-images', '*', '*.jpg'))
    available_images = set()
    for path in image_files:
        parts = path.split(os.sep)
        if len(parts) >= 2:
            set_name = parts[-2].lower()
            card_name = os.path.splitext(parts[-1])[0].lower()
            available_images.add((set_name, card_name))

    def has_image(card):
        set_name = card.get('set_name', '').lower().replace(' ', '-')
        card_name = card.get('card_name', '').lower().replace(' ', '-')
        return (set_name, card_name) in available_images

    if query:
        mongo_query = {
            "$or": [
                {"card_name": {"$regex": query, "$options": "i"}},
                {"set_name": {"$regex": query, "$options": "i"}},
                {"type": {"$regex": query, "$options": "i"}}
            ],
            "card_name": {"$exists": True},
            "set_name": {"$exists": True}
        }
        results = [c for c in cards.find(mongo_query) if has_image(c)]

    # Always show 32 random cards with images
    all_cards_with_images = [c for c in cards.find({"card_name": {"$exists": True}, "set_name": {"$exists": True}}) if has_image(c)]
    import random
    random_cards = random.sample(all_cards_with_images, min(32, len(all_cards_with_images))) if all_cards_with_images else []

    return render_template('search.html', query=query, results=results, random_cards=random_cards)

@app.route('/cardlist.json')
def cardlist_json():
    # Build a set of all available image paths (lowercase, hyphenated)
    image_files = glob.glob(os.path.join(app.root_path, 'static', 'card-front-images', '*', '*.jpg'))
    available_images = set()
    for path in image_files:
        parts = path.split(os.sep)
        if len(parts) >= 2:
            set_name = parts[-2].lower()
            card_name = os.path.splitext(parts[-1])[0].lower()
            available_images.add((set_name, card_name))

    def image_url(card):
        set_name = card.get('set_name', '').lower().replace(' ', '-')
        card_name = card.get('card_name', '').lower().replace(' ', '-')
        if (set_name, card_name) in available_images:
            return url_for('card_image', set_name=set_name, filename=card_name + '.jpg')
        return None

    # Only include cards with images
    cardlist = []
    for c in cards.find({"card_name": {"$exists": True}, "set_name": {"$exists": True}}):
        img = image_url(c)
        if img:
            cardlist.append({
                "card_name": c["card_name"],
                "set_name": c["set_name"],
                "card_url": url_for('card_page', set_slug=c["set_name"].lower().replace(' ', '-'), card_slug=c["card_name"].lower().replace(' ', '-')),
                "image_url": img
            })
    return {"cards": cardlist}

def get_card_name_url_map():
    """Return a dict mapping card names (case-insensitive) to their card page URLs."""
    card_map = {}
    for c in cards.find({"card_name": {"$exists": True}, "set_name": {"$exists": True}}):
        name = c["card_name"]
        url = url_for('card_page', set_slug=c["set_name"].lower().replace(' ', '-'), card_slug=name.lower().replace(' ', '-'))
        # Also store type for coloring
        card_type = c.get("type", "").lower() if c.get("type") else ""
        card_map[name] = {"url": url, "type": card_type}
    return card_map

def autolink_card_names(text, skip_card_name=None):
    card_map = get_card_name_url_map()
    sorted_names = sorted(card_map.keys(), key=lambda n: -len(n))
    # Only autolink in plain text, not inside HTML tags (best effort)
    def replacer(match):
        name = match.group(1)
        # Skip linking if this is the card being reviewed (case-insensitive)
        if skip_card_name and name.lower() == skip_card_name.lower():
            return name
        for k in card_map:
            if k.lower() == name.lower():
                url = card_map[k]["url"]
                ctype = card_map[k]["type"]
                # Build image URL for popover
                set_name = ""
                card_slug = ""
                for c in cards.find({"card_name": {"$regex": f"^{k}$", "$options": "i"}}):
                    set_name = c.get("set_name", "").lower()
                    card_slug = c.get("card_name", "").lower().replace(' ', '-')
                    break
                img_url = f"/card-front-images/{set_name}/{card_slug}.jpg" if set_name and card_slug else ""
                break
        else:
            url = None
            ctype = ""
            img_url = ""
        if url:
            # Build a CSS class for every type word, e.g. "Action - Attack" => card-link-action card-link-action-attack
            type_classes = []
            if ctype:
                for t in re.split(r'[\s\-/]+', ctype):
                    t = t.strip().lower()
                    if t:
                        # For multiword types, join with dash
                        base = ctype.replace(",", "").replace(" ", "-").replace("/", "-").replace("--", "-").lower()
                        # Add both the full type and each part for maximum coverage
                        type_classes.append(f"card-link-{base}")
                        type_classes.append(f"card-link-{t}")
            if not type_classes:
                type_classes = ["card-link-other"]
            # Fix for Python <3.6: avoid nested braces in f-string
            class_str = ' '.join(type_classes)
            data_img = f' data-img="{img_url}"' if img_url else ''
            return f'<a href="{url}" class="card-link {class_str}"{data_img}>{name}</a>'
        return name
    pattern = r'(?<![\w>])(' + '|'.join(re.escape(n) for n in sorted_names) + r')(?![\w<])'
    def link_outside_tags(html):
        parts = re.split(r'(<[^>]+>)', html)
        for i, part in enumerate(parts):
            if not part.startswith('<'):
                parts[i] = re.sub(pattern, replacer, part, flags=re.IGNORECASE)
        return ''.join(parts)
    return link_outside_tags(text)

# Register as Jinja filter
app.jinja_env.filters['autolink_cards'] = autolink_card_names

@app.errorhandler(404)
def page_not_found(e):
    # Build set of all available images (lowercase, hyphenated)
    image_files = glob.glob(os.path.join(app.root_path, 'static', 'card-front-images', '*', '*.jpg'))
    available_images = set()
    for path in image_files:
        parts = path.split(os.sep)
        if len(parts) >= 2:
            set_name = parts[-2].lower()
            card_name = os.path.splitext(parts[-1])[0].lower()
            available_images.add((set_name, card_name))
    def has_image(card):
        set_name = card.get('set_name', '').lower().replace(' ', '-')
        card_name = card.get('card_name', '').lower().replace(' ', '-')
        return (set_name, card_name) in available_images
    all_cards_with_images = [c for c in cards.find({"card_name": {"$exists": True}, "set_name": {"$exists": True}}) if has_image(c)]
    import random
    random_cards = random.sample(all_cards_with_images, min(16, len(all_cards_with_images))) if all_cards_with_images else []
    return render_template('404.html', random_cards=random_cards), 404


# --- Sitemap route for SEO ---
from flask import Response
@app.route('/sitemap.xml')
def sitemap():
    import datetime
    base_url = request.url_root.rstrip('/')
    urls = [
        {'loc': base_url + url_for('index'), 'priority': '1.0'},
        {'loc': base_url + url_for('search'), 'priority': '0.8'},
    ]
    # Add all card pages
    for c in cards.find({"card_name": {"$exists": True}, "set_name": {"$exists": True}}):
        set_slug = c["set_name"].lower().replace(' ', '-')
        card_slug = c["card_name"].lower().replace(' ', '-')
        url = base_url + url_for('card_page', set_slug=set_slug, card_slug=card_slug)
        urls.append({'loc': url, 'priority': '0.7'})
    # Build XML
    lastmod = datetime.datetime.utcnow().date().isoformat()
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for u in urls:
        xml.append('  <url>')
        xml.append(f'    <loc>{u["loc"]}</loc>')
        xml.append(f'    <lastmod>{lastmod}</lastmod>')
        xml.append(f'    <priority>{u["priority"]}</priority>')
        xml.append('  </url>')
    xml.append('</urlset>')
    return Response('\n'.join(xml), mimetype='application/xml')

@app.route('/unanalyzed')
def unanalyzed_cards():
    # Build set of all available images (lowercase, hyphenated)
    image_files = glob.glob(os.path.join(app.root_path, 'static', 'card-front-images', '*', '*.jpg'))
    available_images = set()
    for path in image_files:
        parts = path.split(os.sep)
        if len(parts) >= 2:
            set_name = parts[-2].lower()
            card_name = os.path.splitext(parts[-1])[0].lower()
            available_images.add((set_name, card_name))

    def has_image(card):
        set_name = card.get('set_name', '').lower().replace(' ', '-')
        card_name = card.get('card_name', '').lower().replace(' ', '-')
        return (set_name, card_name) in available_images

    # Get cards without strategy reviews but with images
    unanalyzed_cards = [
        c for c in cards.find({
            "card_name": {"$exists": True}, 
            "set_name": {"$exists": True}, 
            "strategy_review": {"$exists": False}
        }) if has_image(c)
    ]
    
    return render_template('unanalyzed.html', unanalyzed_cards=unanalyzed_cards)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)


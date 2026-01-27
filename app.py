"""
Travel Buddy Pro v3.0 - Intelligent Life Companion
An AI assistant that thinks like a friend, anticipates needs, and chains activities.
"""

import os
import json
import requests
import hashlib
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient

# BeautifulSoup for scraping BookMyShow
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
    print("✅ BeautifulSoup Active - Web scraping enabled")
except ImportError:
    HAS_BS4 = False
    print("⚠️ BeautifulSoup not installed")

# --- GEOPY SETUP ---
try:
    from geopy.geocoders import Nominatim
    geolocator = Nominatim(user_agent="travel_buddy_v3")
    HAS_GEOPY = True
    print("✅ Geopy Active - Location intelligence enabled")
except ImportError:
    HAS_GEOPY = False
    print("⚠️ Geopy not installed")

app = Flask(__name__)
# CORS configuration - allow all origins without credentials
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "allow_headers": ["Content-Type", "Accept", "Authorization", "X-Requested-With"],
    "methods": ["GET", "POST", "OPTIONS"]
}})


# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY")  # Free tier
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")  # Free tier
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY")  # For theaters/restaurants

# In-memory user preferences store (persists during server lifetime)
# For production, use Redis or a database
USER_PREFERENCES = {}

tavily = None
if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)
    print("✅ Tavily Active - Real-time search enabled")

# ==================================================
# CONVERSATION STATE MACHINE (NEW)
# ==================================================

CLARIFYING_QUESTIONS = {
    "movie": {
        "questions": [
            {
                "id": "food_timing",
                "text": "Want to eat before or after the movie?",
                "options": [
                    {"label": "🍽️ Before", "value": "before"},
                    {"label": "🎬 After", "value": "after"},
                    {"label": "🍿 Just snacks", "value": "snacks"},
                    {"label": "🚫 Not hungry", "value": "none"}
                ]
            },
            {
                "id": "food_type",
                "text": "What kind of food?",
                "options": [
                    {"label": "🍕 Quick bite", "value": "quick", "search_mod": "fast food quick service"},
                    {"label": "🍝 Something nice", "value": "nice", "search_mod": "good restaurant dine in"},
                    {"label": "🌮 Street food", "value": "street", "search_mod": "popular street food local famous"},
                    {"label": "☕ Cafe vibes", "value": "cafe", "search_mod": "cafe coffee snacks"}
                ],
                "depends_on": {"food_timing": ["before", "after"]}
            },
            {
                "id": "movie_pref",
                "text": "What are you in the mood for?",
                "options": [
                    {"label": "🎬 New releases", "value": "new"},
                    {"label": "⭐ Top rated", "value": "top"},
                    {"label": "😂 Comedy", "value": "comedy"},
                    {"label": "💥 Action", "value": "action"},
                    {"label": "🤷 Surprise me", "value": "any"}
                ]
            }
        ]
    },
    "food": {
        "questions": [
            {
                "id": "food_type",
                "text": "What kind of food are you craving?",
                "options": [
                    {"label": "🍕 Quick bite", "value": "quick"},
                    {"label": "🍝 Proper meal", "value": "proper"},
                    {"label": "🌮 Street food", "value": "street"},
                    {"label": "☕ Cafe", "value": "cafe"},
                    {"label": "🍻 Drinks + food", "value": "pub"}
                ]
            },
            {
                "id": "budget",
                "text": "Budget?",
                "options": [
                    {"label": "💰 Under ₹500", "value": "budget"},
                    {"label": "💳 ₹500-1500", "value": "mid"},
                    {"label": "💎 No limit", "value": "premium"}
                ]
            }
        ]
    },
    "bored": {
        "questions": [
            {
                "id": "time_available",
                "text": "How much time do you have?",
                "options": [
                    {"label": "⚡ 1-2 hours", "value": "short"},
                    {"label": "🕐 Half day", "value": "half"},
                    {"label": "📅 Full day", "value": "full"}
                ]
            },
            {
                "id": "energy_level",
                "text": "Energy level?",
                "options": [
                    {"label": "🧘 Chill", "value": "chill"},
                    {"label": "⚖️ Balanced", "value": "balanced"},
                    {"label": "🎢 Adventurous", "value": "adventure"}
                ]
            }
        ]
    },
    "day_plan": {
        "questions": [
            {
                "id": "start_time",
                "text": "What time do you want to start?",
                "options": [
                    {"label": "🌅 Morning (~9 AM)", "value": "morning"},
                    {"label": "☀️ Noon (~12 PM)", "value": "noon"},
                    {"label": "🌆 Evening (~5 PM)", "value": "evening"}
                ]
            },
            {
                "id": "vibe",
                "text": "What's the vibe for the day?",
                "options": [
                    {"label": "🧘 Chill & Relaxed", "value": "chill"},
                    {"label": "🎯 Active & Fun", "value": "active"},
                    {"label": "💕 Romantic", "value": "romantic"},
                    {"label": "🎉 Party mode", "value": "party"}
                ]
            },
            {
                "id": "must_include",
                "text": "Must include?",
                "type": "multi_select",
                "options": [
                    {"label": "🎬 Movie", "value": "movie"},
                    {"label": "🍽️ Nice meal", "value": "meal"},
                    {"label": "🛍️ Shopping", "value": "shopping"},
                    {"label": "🌳 Outdoors", "value": "outdoors"},
                    {"label": "☕ Cafe time", "value": "cafe"}
                ]
            },
            {
                "id": "budget",
                "text": "Budget for the day?",
                "options": [
                    {"label": "💰 Under ₹1000", "value": "budget"},
                    {"label": "💳 ₹1000-3000", "value": "mid"},
                    {"label": "💎 No limit", "value": "premium"}
                ]
            }
        ]
    },
    "date": {
        "questions": [
            {
                "id": "date_type",
                "text": "What kind of date?",
                "options": [
                    {"label": "🍽️ Dinner date", "value": "dinner"},
                    {"label": "🎬 Movie + dinner", "value": "movie_dinner"},
                    {"label": "🌆 Full day out", "value": "full_day"},
                    {"label": "🌃 Night out", "value": "nightlife"}
                ]
            },
            {
                "id": "budget",
                "text": "Budget?",
                "options": [
                    {"label": "💳 Normal", "value": "mid"},
                    {"label": "💎 Splurge", "value": "premium"}
                ]
            }
        ]
    }
}

# Detect planning mode from query
def detect_planning_mode(query):
    """Detects if user wants spontaneous, day planning, or trip planning."""
    query_lower = query.lower()
    
    # Day planning triggers
    day_triggers = ["plan my", "plan the day", "full day", "whole day", "saturday", "sunday", "tomorrow", "today's plan"]
    for trigger in day_triggers:
        if trigger in query_lower:
            return "day_plan"
    
    # Trip planning triggers
    trip_triggers = ["trip to", "days in", "week in", "travel to", "vacation", "holiday"]
    for trigger in trip_triggers:
        if trigger in query_lower:
            return "trip_plan"
    
    return "spontaneous"


def get_next_question(intent, answers):
    """Returns the next unanswered question based on current answers."""
    questions = CLARIFYING_QUESTIONS.get(intent, {}).get("questions", [])
    
    for q in questions:
        # Skip if already answered
        if q["id"] in answers:
            continue
        
        # Check dependencies
        depends = q.get("depends_on", {})
        skip = False
        for dep_id, dep_values in depends.items():
            if answers.get(dep_id) not in dep_values:
                skip = True
                break
        
        if not skip:
            return q
    
    return None  # All questions answered


def is_conversation_complete(intent, answers):
    """Checks if all required questions have been answered."""
    return get_next_question(intent, answers) is None



# ==================================================
# INTENT RECOGNITION SYSTEM
# ==================================================

INTENT_PATTERNS = {
    "movie": {
        "keywords": ["movie", "film", "cinema", "watch", "theatre", "theater", "show", "multiplex", "pvr", "inox"],
        "emoji": "🎬",
        "chain": ["movie", "food_check", "transport"],
        "duration_mins": 180  # avg movie + travel
    },
    "food": {
        "keywords": ["hungry", "eat", "food", "restaurant", "dinner", "lunch", "breakfast", "cafe", "coffee", "snack", "biryani", "pizza", "burger", "dhaba"],
        "emoji": "🍽️",
        "chain": ["food"],
        "duration_mins": 90
    },
    "bored": {
        "keywords": ["bored", "nothing to do", "kill time", "free time", "what to do", "suggest", "ideas"],
        "emoji": "😴",
        "chain": ["quick_activity", "food_check"],
        "duration_mins": 120
    },
    "date": {
        "keywords": ["date", "romantic", "anniversary", "special", "girlfriend", "boyfriend", "partner", "wife", "husband", "couple"],
        "emoji": "💕",
        "chain": ["romantic_dinner", "activity", "dessert"],
        "duration_mins": 240
    },
    "explore": {
        "keywords": ["explore", "discover", "what's around", "nearby", "visit", "sightseeing", "tourist"],
        "emoji": "🗺️",
        "chain": ["explore", "food_check"],
        "duration_mins": 180
    },
    "chill": {
        "keywords": ["chill", "relax", "calm", "peaceful", "quiet", "unwind", "destress"],
        "emoji": "🧘",
        "chain": ["chill_spot", "cafe"],
        "duration_mins": 120
    },
    "adventure": {
        "keywords": ["adventure", "thrill", "exciting", "adrenaline", "fun", "activity", "sports"],
        "emoji": "🎢",
        "chain": ["adventure", "food_check"],
        "duration_mins": 180
    },
    "shopping": {
        "keywords": ["shopping", "mall", "buy", "shop", "market", "store"],
        "emoji": "🛍️",
        "chain": ["shopping", "food_check"],
        "duration_mins": 180
    },
    "nightlife": {
        "keywords": ["club", "bar", "pub", "night", "party", "drinks", "beer", "cocktail", "lounge"],
        "emoji": "🍸",
        "chain": ["dinner", "nightlife"],
        "duration_mins": 240
    }
}

def detect_intent(user_input):
    """Detects user intent from free-form input with confidence scores."""
    if not user_input:
        return [{"intent": "explore", "confidence": 0.5}]
    
    user_lower = user_input.lower()
    detected = []
    
    for intent, data in INTENT_PATTERNS.items():
        matches = sum(1 for kw in data["keywords"] if kw in user_lower)
        if matches > 0:
            confidence = min(0.9, 0.3 + (matches * 0.2))
            detected.append({
                "intent": intent,
                "confidence": confidence,
                "emoji": data["emoji"],
                "chain": data["chain"],
                "duration": data["duration_mins"]
            })
    
    # Sort by confidence
    detected.sort(key=lambda x: x["confidence"], reverse=True)
    
    return detected if detected else [{"intent": "explore", "confidence": 0.5, "emoji": "🗺️", "chain": ["explore"], "duration": 180}]


    return detected if detected else [{"intent": "explore", "confidence": 0.5, "emoji": "🗺️", "chain": ["explore"], "duration": 180}]


# ==================================================
# SELECTION & ITINERARY ENDPOINTS (NEW)
# ==================================================

@app.route('/api/options', methods=['POST'])
def get_options():
    """Returns selectable options for a specific step (e.g. 'movie' or 'food')."""
    try:
        data = request.json
        category = data.get('category')  # "movie", "food", "activity"
        city = resolve_location(data)
        context = data.get('context', {})
        preferences = data.get('preferences', {})
        
        print(f"🔍 Fetching options for: {category} in {city}")
        
        results = []
        
        if category == 'movie':
            # Priority 1: TMDB API (most reliable for current movies)
            results = get_tmdb_now_playing()
            
            # Priority 2: Fallback to scraping if TMDB fails
            if not results:
                movies = get_now_playing_movies(city)
                for m in movies:
                    results.append({
                        "id": hashlib.md5(m.get('title', 'unknown').encode()).hexdigest(),
                        "title": m.get('title', 'Unknown Movie'),
                        "subtitle": m.get('source', 'Unknown Source'),
                        "emoji": "🎬",
                        "details": "Check showtimes",
                        "value": m.get('title', 'Unknown')
                    })
                
        elif category == 'food':
            # Search for food based on preferences
            food_type = preferences.get('food_type', 'best rated')
            query = f"{food_type} restaurants in {city} open now"
            if tavily:
                tavily_results = tavily.search(query=query, max_results=6)
                for res in tavily_results.get('results', []):
                    results.append({
                        "id": hashlib.md5(res['content'].encode()).hexdigest(),
                        "title": res['title'].split(':')[0], # Clean title
                        "subtitle": res['content'][:60] + "...",
                        "emoji": "🍽️",
                        "details": "Highly rated",
                        "value": res['title']
                    })
        
        elif category == 'activity':
            # Search for things to do
            query = f"fun things to do in {city} right now"
            if tavily:
                tavily_results = tavily.search(query=query, max_results=6)
                for res in tavily_results.get('results', []):
                    results.append({
                        "id": hashlib.md5(res['title'].encode()).hexdigest(),
                        "title": res['title'],
                        "subtitle": res['content'][:60] + "...",
                        "emoji": "✨",
                        "details": "Recommended",
                        "value": res['title']
                    })
        
        if not results:
            print(f"⚠️ No results found for {category}, adding fallback options")
            if category == 'movie':
                results = [
                    {"id": "mock1", "title": "Inception (Re-release)", "subtitle": "IMAX 2D", "emoji": "🎬", "details": "4.5 ★ • Sci-Fi", "value": "Inception"},
                    {"id": "mock2", "title": "The Dark Knight", "subtitle": "Special Screening", "emoji": "🦇", "details": "4.9 ★ • Action", "value": "The Dark Knight"},
                    {"id": "mock3", "title": "Interstellar", "subtitle": "IMAX Experience", "emoji": "🚀", "details": "4.8 ★ • Sci-Fi", "value": "Interstellar"},
                ]
            elif category == 'food':
                results = [
                    {"id": "mock_f1", "title": "Pizza Express", "subtitle": "Italian • Pizza", "emoji": "🍕", "details": "4.4 ★ • $$", "value": "Pizza Express"},
                    {"id": "mock_f2", "title": "Burger King", "subtitle": "Fast Food • Burgers", "emoji": "🍔", "details": "4.1 ★ • $", "value": "Burger King"},
                    {"id": "mock_f3", "title": "Starbucks", "subtitle": "Coffee • Snacks", "emoji": "☕", "details": "4.3 ★ • $$", "value": "Starbucks"},
                ]
            elif category == 'activity':
                results = [
                    {"id": "mock_a1", "title": "City Walk", "subtitle": "Explore downtown", "emoji": "🚶", "details": "Free • 1-2 hours", "value": "City Walk"},
                    {"id": "mock_a2", "title": "Local Museum", "subtitle": "History & Art", "emoji": "🏛️", "details": "Ticketed • 2 hours", "value": "Local Museum"},
                ]
        
        return jsonify({
            "category": category,
            "options": results
        })

    except Exception as e:
        print(f"❌ Options Error: {e}")
        return jsonify({"options": []})


# ==================================================
# WIZARD ENDPOINTS (Phase 7)
# ==================================================

@app.route('/api/wizard/movies', methods=['POST'])
def wizard_movies():
    """Returns movies with showtimes using Gemini AI + Google Places theaters."""
    try:
        data = request.json
        timing = data.get('timing', 'now')
        city = data.get('city', 'Delhi')
        lat = data.get('lat')
        lng = data.get('lng')
        
        now = datetime.now()
        current_hour = now.hour
        today_str = now.strftime("%B %d, %Y")
        
        print(f"🎬 Wizard: {city}, timing={timing}, coords=({lat},{lng}), time={current_hour}:{now.minute:02d}")
        
        # Step 1: Get REAL nearby theaters using Google Places
        theaters = []
        if lat and lng and GOOGLE_PLACES_API_KEY:
            theaters = get_nearby_theaters(float(lat), float(lng))
            print(f"📍 Found {len(theaters)} nearby theaters")
        
        # Fallback theaters
        if not theaters:
            theaters = [
                {"name": f"PVR {city}", "address": city, "lat": lat, "lng": lng},
                {"name": f"INOX {city}", "address": city, "lat": lat, "lng": lng},
                {"name": f"Cinepolis {city}", "address": city, "lat": lat, "lng": lng}
            ]
        
        # Step 2: Get movies from Gemini AI (since Tavily doesn't work for showtimes)
        movies = []
        try:
            prompt = f"""List 5 movies currently playing in theaters in India as of {today_str}.
Return ONLY a JSON array with this format, no other text:
[
  {{"title": "Movie Name", "rating": "8.5", "genre": "Action"}}
]
Include only movies that would realistically be in theaters NOW. Use real ratings from IMDB or similar."""
            
            response = ask_gemini(prompt)
            # Clean and parse JSON
            json_str = response.strip()
            if json_str.startswith('```'):
                json_str = json_str.split('```')[1]
                if json_str.startswith('json'):
                    json_str = json_str[4:]
            movie_list = json.loads(json_str.strip())
            
            # Generate showtimes at real theaters
            base_times = []
            # Generate times starting from current hour
            for h in range(current_hour, 24):
                if h >= 10:  # Movies start from 10 AM
                    for m in [0, 30]:
                        if h > current_hour or (h == current_hour and m > now.minute):
                            period = "PM" if h >= 12 else "AM"
                            hour_12 = h if h <= 12 else h - 12
                            if hour_12 == 0:
                                hour_12 = 12
                            base_times.append(f"{hour_12}:{m:02d} {period}")
                        if len(base_times) >= 4:
                            break
                if len(base_times) >= 4:
                    break
            
            if not base_times:
                base_times = ["7:00 PM", "9:30 PM"]
            
            for movie_data in movie_list[:5]:
                showtimes = []
                for i, theater in enumerate(theaters[:3]):
                    theater_name = theater.get('name', 'Theater')
                    theater_lat = theater.get('lat', lat)
                    theater_lng = theater.get('lng', lng)
                    maps_url = f"https://www.google.com/maps/search/?api=1&query={theater_lat},{theater_lng}"
                    
                    # Use different times for different theaters
                    time_idx = i % len(base_times)
                    showtimes.append({
                        "time": base_times[time_idx],
                        "theater": theater_name,
                        "address": theater.get('address', ''),
                        "maps_url": maps_url
                    })
                
                movies.append({
                    "title": movie_data.get('title', 'Movie'),
                    "rating": movie_data.get('rating', '7.5'),
                    "genre": movie_data.get('genre', 'Movie'),
                    "showtimes": showtimes
                })
                
        except Exception as e:
            print(f"⚠️ Gemini movie error: {e}")
        
        # Fallback: Show theaters with manual input
        if not movies:
            movies = [{
                "title": "🔍 Type your movie below",
                "rating": "-",
                "genre": "Theaters near you:",
                "showtimes": [{
                    "time": "Check on BookMyShow",
                    "theater": t.get('name', 'Theater'),
                    "maps_url": f"https://www.google.com/maps/search/?api=1&query={t.get('lat', lat)},{t.get('lng', lng)}"
                } for t in theaters[:3]]
            }]
        
        return jsonify({
            "movies": movies,
            "theaters": [{"name": t.get('name'), "maps_url": f"https://www.google.com/maps/search/?api=1&query={t.get('lat', lat)},{t.get('lng', lng)}"} for t in theaters[:5]]
        })
        
    except Exception as e:
        print(f"❌ Wizard Movies Error: {e}")
        return jsonify({"movies": [], "theaters": []})


@app.route('/api/wizard/itinerary', methods=['POST'])
def wizard_itinerary():
    """Generates a complete itinerary with specific parking from Google Places."""
    try:
        data = request.json
        movie = data.get('movie', 'Movie')
        showtime = data.get('showtime', '7:00 PM')
        theater = data.get('theater', 'Theater')
        food = data.get('food', 'skip')
        cuisine = data.get('cuisine', 'any')
        travel = data.get('travel', 'car')
        city = data.get('city', 'Delhi')
        lat = data.get('lat')
        lng = data.get('lng')
        
        # Get CURRENT time for planning
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        
        print(f"📝 Wizard: Generating itinerary - {movie} at {theater} {showtime}, current time: {current_hour}:{current_minute:02d}")
        
        # Parse showtime to calculate travel time
        try:
            show_hour = int(showtime.split(':')[0])
            if 'PM' in showtime.upper() and show_hour != 12:
                show_hour += 12
        except:
            show_hour = 19  # Default to 7 PM
        
        # Calculate start time (30 mins before showtime for travel)
        start_hour = show_hour - 1 if show_hour > current_hour else current_hour
        start_minute = 0
        
        # If show is in the past or too soon, adjust
        if show_hour <= current_hour:
            start_hour = current_hour
            start_minute = (current_minute // 15 + 1) * 15  # Round up to next 15 min
            if start_minute >= 60:
                start_hour += 1
                start_minute = 0
        
        def format_time(h, m):
            period = "PM" if h >= 12 else "AM"
            hour_12 = h if h <= 12 else h - 12
            if hour_12 == 0:
                hour_12 = 12
            return f"{hour_12}:{m:02d} {period}"
        
        steps = []
        current_time_mins = start_hour * 60 + start_minute
        
        # Step 1: Leave from home
        steps.append({
            "time": format_time(start_hour, start_minute),
            "action": "🚗 Leave from home",
            "detail": f"Head to {theater}, {city}",
            "editable": False
        })
        current_time_mins += 25  # 25 min travel
        
        # Step 2: Parking (specific from Google Places)
        if travel == 'car':
            parking_name = f"{theater} Parking"
            parking_detail = f"Parking at {theater}"
            parking_lat = lat
            parking_lng = lng
            
            # Try to get specific parking from Google Places
            if lat and lng and GOOGLE_PLACES_API_KEY:
                parking_spots = get_nearby_parking(float(lat), float(lng))
                if parking_spots:
                    parking_name = f"🅿️ {parking_spots[0].get('name', 'Parking')}"
                    parking_detail = parking_spots[0].get('address', 'Near theater')
                    parking_lat = parking_spots[0].get('lat', lat)
                    parking_lng = parking_spots[0].get('lng', lng)
            
            steps.append({
                "time": format_time(current_time_mins // 60, current_time_mins % 60),
                "action": parking_name,
                "detail": parking_detail,
                "maps_url": f"https://www.google.com/maps/search/?api=1&query={parking_lat},{parking_lng}",
                "editable": True,
                "type": "parking"
            })
            current_time_mins += 10  # 10 min to park and walk
        
        # Step 3: Food (before movie if selected)
        if food == 'before':
            food_name = "🍔 Quick bite"
            food_detail = f"Near {theater}"
            food_lat = lat
            food_lng = lng
            
            # Try to get restaurant from Google Places
            if lat and lng and GOOGLE_PLACES_API_KEY:
                restaurants = get_nearby_restaurants(float(lat), float(lng), cuisine if cuisine != 'any' else None)
                if restaurants:
                    food_name = f"🍽️ {restaurants[0].get('name', 'Restaurant')}"
                    food_detail = f"{restaurants[0].get('address', '')} • ⭐ {restaurants[0].get('rating', 'N/A')}"
                    food_lat = restaurants[0].get('lat', lat)
                    food_lng = restaurants[0].get('lng', lng)
            
            steps.append({
                "time": format_time(current_time_mins // 60, current_time_mins % 60),
                "action": food_name,
                "detail": food_detail,
                "maps_url": f"https://www.google.com/maps/search/?api=1&query={food_lat},{food_lng}",
                "editable": True,
                "type": "restaurant"
            })
            current_time_mins += 30  # 30 min to eat
        
        # Step 4: Movie
        steps.append({
            "time": showtime,
            "action": f"🎬 {movie}",
            "detail": theater,
            "editable": False
        })
        current_time_mins = show_hour * 60 + 150  # ~2.5 hours for movie
        
        # Step 5: Food (after movie if selected)
        if food == 'after':
            food_name = "🍽️ Dinner"
            food_detail = f"Near {theater}"
            food_lat = lat
            food_lng = lng
            
            if lat and lng and GOOGLE_PLACES_API_KEY:
                restaurants = get_nearby_restaurants(float(lat), float(lng), cuisine if cuisine != 'any' else None)
                if restaurants:
                    food_name = f"🍽️ {restaurants[0].get('name', 'Restaurant')}"
                    food_detail = f"{restaurants[0].get('address', '')} • ⭐ {restaurants[0].get('rating', 'N/A')}"
                    food_lat = restaurants[0].get('lat', lat)
                    food_lng = restaurants[0].get('lng', lng)
            
            steps.append({
                "time": format_time(current_time_mins // 60, current_time_mins % 60),
                "action": food_name,
                "detail": food_detail,
                "maps_url": f"https://www.google.com/maps/search/?api=1&query={food_lat},{food_lng}",
                "editable": True,
                "type": "restaurant"
            })
            current_time_mins += 45  # 45 min to eat
        
        # Step 6: Head home
        steps.append({
            "time": format_time(current_time_mins // 60, current_time_mins % 60),
            "action": "🏠 Head home",
            "detail": "End of plan",
            "editable": False
        })
        
        # Calculate total duration
        total_mins = current_time_mins - (start_hour * 60 + start_minute)
        hours = total_mins // 60
        mins = total_mins % 60
        duration = f"~{hours}h {mins}m" if mins else f"~{hours} hours"
        
        return jsonify({
            "steps": steps,
            "duration": duration,
            "cost": "₹1,200 estimated"
        })
        
    except Exception as e:
        print(f"❌ Wizard Itinerary Error: {e}")
        # Better fallback with current time
        now = datetime.now()
        return jsonify({
            "steps": [
                {"time": f"{now.hour}:{now.minute:02d}", "action": "🚗 Leave now", "detail": f"Head to {data.get('theater', 'theater')}", "editable": False},
                {"time": data.get('showtime', '7:00 PM'), "action": f"🎬 {data.get('movie', 'Movie')}", "detail": data.get('theater', 'Theater'), "editable": False},
                {"time": "After movie", "action": "🏠 Head home", "detail": "End of plan", "editable": False}
            ],
            "duration": "~3 hours",
            "cost": "₹1,000 estimated"
        })


@app.route('/api/itinerary', methods=['POST'])
def generate_itinerary():
    """Generates a final itinerary based on SELECTED items."""
    try:
        data = request.json
        selections = data.get('selections', {})
        city = resolve_location(data)
        
        print(f"📝 Generating itinerary for selections: {selections}")
        
        # Build prompt for Gemini
        prompt = f"""
        Create a detailed timed itinerary for someone in {city}.
        
        THEIR SELECTIONS:
        {json.dumps(selections, indent=2)}
        
        Start time: Now ({datetime.now().strftime('%I:%M %p')})
        
        create a JSON response with:
        {{
            "title": "Your Custom Plan",
            "total_duration": "e.g. 4 hours",
            "timeline": [
                {{
                    "time": "5:00 PM",
                    "action": "Head to [Place]",
                    "description": "Details...",
                    "emoji": "🚗"
                }}
            ],
            "google_maps_link": "https://maps.google.com/..."
        }}
        """
        
        response = ask_gemini(prompt)
        return jsonify(json.loads(response))
        
    except Exception as e:
        print(f"❌ Itinerary Error: {e}")
        return jsonify({"error": str(e)})


# ==================================================
# ACTIVITY CHAINING LOGIC
# ==================================================

def calculate_food_need(current_hour, activity_duration_mins):
    """
    Determines if user needs food before/after activity.
    Returns: 'lunch_before', 'lunch_after', 'dinner_before', 'dinner_after', 'snack', or None
    """
    end_hour = current_hour + (activity_duration_mins / 60)
    
    # Meal windows (24h format)
    BREAKFAST = (7, 10)
    LUNCH = (12, 14.5)
    SNACK = (16, 18)
    DINNER = (19, 21.5)
    
    # Will they finish during/after a meal window?
    if LUNCH[0] <= end_hour <= LUNCH[1] + 0.5:
        return "lunch_after"
    if DINNER[0] <= end_hour <= DINNER[1] + 0.5:
        return "dinner_after"
    
    # Are they starting during a meal window?
    if LUNCH[0] <= current_hour <= LUNCH[1]:
        return "lunch_before"
    if DINNER[0] <= current_hour <= DINNER[1]:
        return "dinner_before"
    
    # Snack time?
    if SNACK[0] <= current_hour <= SNACK[1]:
        return "snack"
    if SNACK[0] <= end_hour <= SNACK[1]:
        return "snack_after"
    
    return None


def build_activity_chain(primary_intent, context):
    """
    Builds a complete activity chain with timing logic.
    Example: movie at 7pm → dinner after (ends ~10pm)
    """
    chain = []
    current_hour = context.get('local_hour', 12)
    intent_data = INTENT_PATTERNS.get(primary_intent, INTENT_PATTERNS["explore"])
    
    # Add primary activity
    chain.append({
        "type": primary_intent,
        "priority": "primary",
        "search_query": None  # Will be built later
    })
    
    # Check food needs
    food_need = calculate_food_need(current_hour, intent_data["duration_mins"])
    
    if food_need:
        position = "before" if "before" in food_need else "after"
        meal_type = food_need.replace("_before", "").replace("_after", "")
        
        chain.append({
            "type": "food",
            "subtype": meal_type,
            "priority": "anticipated",
            "position": position,
            "reason": f"You'll probably want {meal_type} {position} your activity"
        })
    
    # Special chains for specific intents
    if primary_intent == "movie":
        # Check if late show
        if current_hour >= 19:
            chain.append({
                "type": "late_night_food",
                "priority": "anticipated",
                "position": "after",
                "reason": "Perfect for a post-movie meal!"
            })
    
    if primary_intent == "date":
        # Always add dessert for date nights
        chain.append({
            "type": "dessert",
            "priority": "bonus",
            "position": "after",
            "reason": "End the night on a sweet note 🍰"
        })
    
    return chain


# ==================================================
# WEATHER INTEGRATION (Free OpenWeatherMap)
# ==================================================

def get_weather(city, country_code="IN"):
    """Fetches current weather for smart suggestions."""
    if not OPENWEATHER_API_KEY:
        return None
    
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city},{country_code}&appid={OPENWEATHER_API_KEY}&units=metric"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                "temp": round(data["main"]["temp"]),
                "feels_like": round(data["main"]["feels_like"]),
                "condition": data["weather"][0]["main"],
                "description": data["weather"][0]["description"],
                "is_rainy": data["weather"][0]["main"].lower() in ["rain", "drizzle", "thunderstorm"],
                "is_hot": data["main"]["temp"] > 35,
                "is_cold": data["main"]["temp"] < 15
            }
    except Exception as e:
        print(f"⚠️ Weather fetch failed: {e}")
    return None


# ==================================================
# GOOGLE PLACES API (Theaters & Restaurants)
# ==================================================

def google_nearby_search(lat, lng, place_type, keyword=None, radius=5000):
    """
    Searches for nearby places using Google Places API.
    place_type: 'movie_theater', 'restaurant', 'parking', etc.
    Returns list of places with name, address, rating, location.
    """
    if not GOOGLE_PLACES_API_KEY:
        print("⚠️ Google Places API key not configured")
        return []
    
    try:
        url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        params = {
            "location": f"{lat},{lng}",
            "radius": radius,
            "type": place_type,
            "key": GOOGLE_PLACES_API_KEY
        }
        if keyword:
            params["keyword"] = keyword
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data.get("status") == "OK":
            places = []
            for place in data.get("results", [])[:10]:
                places.append({
                    "name": place.get("name"),
                    "address": place.get("vicinity"),
                    "rating": place.get("rating", "N/A"),
                    "lat": place["geometry"]["location"]["lat"],
                    "lng": place["geometry"]["location"]["lng"],
                    "place_id": place.get("place_id"),
                    "open_now": place.get("opening_hours", {}).get("open_now", True)
                })
            print(f"✅ Google Places: Found {len(places)} {place_type}s")
            return places
        else:
            print(f"⚠️ Google Places error: {data.get('status')}")
            
    except Exception as e:
        print(f"❌ Google Places failed: {e}")
    
    return []


def get_nearby_theaters(lat, lng):
    """Gets movie theaters near the user's location."""
    return google_nearby_search(lat, lng, "movie_theater")


def get_nearby_restaurants(lat, lng, cuisine=None):
    """Gets restaurants near a location, optionally filtered by cuisine."""
    keyword = cuisine if cuisine else None
    return google_nearby_search(lat, lng, "restaurant", keyword=keyword)


def get_nearby_parking(lat, lng):
    """Gets parking options near a location."""
    return google_nearby_search(lat, lng, "parking", radius=1000)


# ==================================================
# TMDB LIVE MOVIE DATA (Now Playing)
# ==================================================

def get_tmdb_now_playing():
    """Fetches currently playing movies in India from TMDB API."""
    if not TMDB_API_KEY:
        print("⚠️ TMDB API key not configured")
        return []
    
    try:
        # TMDB now_playing endpoint for India region
        url = f"https://api.themoviedb.org/3/movie/now_playing?api_key={TMDB_API_KEY}&region=IN&language=en-IN&page=1"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            movies = []
            
            for movie in data.get('results', [])[:8]:  # Top 8 movies
                movies.append({
                    "id": str(movie['id']),
                    "title": movie['title'],
                    "subtitle": f"⭐ {movie['vote_average']:.1f} • {movie.get('release_date', 'New')[:4]}",
                    "emoji": "🎬",
                    "details": movie.get('overview', '')[:80] + "...",
                    "value": movie['title'],
                    "poster": f"https://image.tmdb.org/t/p/w200{movie['poster_path']}" if movie.get('poster_path') else None
                })
            
            print(f"✅ TMDB: Found {len(movies)} now playing movies")
            return movies
        else:
            print(f"⚠️ TMDB API returned {response.status_code}")
            
    except Exception as e:
        print(f"⚠️ TMDB fetch failed: {e}")
    
    return []


# ==================================================
# BOOKMYSHOW SCRAPER (Live Data)
# ==================================================

def scrape_bookmyshow(city):
    """Scrapes BookMyShow for currently playing movies."""
    if not HAS_BS4:
        print("⚠️ BS4 missing, skipping scraping")
        return []
        
    try:
        # Step 1: Find city code/url via search (simulated for simplicity)
        # Using a direct search for "BookMyShow {city} movies" via Tavily to get URL
        if not tavily:
            return []
            
        search_query = f"BookMyShow movies {city} now showing page"
        search_result = tavily.search(query=search_query, max_results=1)
        
        bms_url = None
        if search_result.get('results'):
            bms_url = search_result['results'][0]['url']
        
        if not bms_url or "bookmyshow.com" not in bms_url:
            print("⚠️ Coudln't find BMS URL")
            return []
            
        print(f"🕷️ Scraping: {bms_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(bms_url, headers=headers, timeout=5)
        
        if response.status_code != 200:
            return []
            
        soup = BeautifulSoup(response.content, 'html.parser')
        movies = []
        
        # BMS structure often changes, looking for common movie card classes
        # This is a generic scraper attempting to find movie titles
        # Looking for standard card structures
        cards = soup.find_all('div', class_=re.compile(r'style__StyledText-sc-7o7nez-0'))
        
        # Fallback to general title search if specific classes fail
        if not cards:
            title_tags = soup.find_all(['h3', 'h4', 'div'], string=re.compile(r'.+'))
            seen = set()
            for tag in title_tags:
                text = tag.get_text().strip()
                # Filter noise
                if len(text) > 3 and len(text) < 50 and text not in seen:
                    # Basic heuristics to identify potential movie titles
                    if not any(x in text.lower() for x in ['movies', 'events', 'plays', 'activities', 'privacy', 'contact']):
                        seen.add(text)
                        movies.append({
                            "title": text,
                            "url": bms_url,
                            "source": "BookMyShow"
                        })
                        if len(movies) >= 8: break
        
        return movies
        
    except Exception as e:
        print(f"⚠️ Scraping failed: {e}")
        return []

# ==================================================
# MOVIE SEARCH (Hybrid: Scraper + Tavily)
# ==================================================

def get_now_playing_movies(city):
    """Gets currently playing movies (tries scraping first, then Tavily)."""
    
    # 1. Try scraping first for REAL live data
    scraped_movies = scrape_bookmyshow(city)
    if scraped_movies:
        print(f"✅ Found {len(scraped_movies)} movies via scraping")
        return scraped_movies
    
    # 2. Fallback to Tavily
    if not tavily:
        return []
    
    try:
        # Include current date to get fresh results
        today = datetime.now().strftime("%B %Y")  # e.g., "January 2026"
        query = f"movies playing in {city} {today} bookmyshow showtimes"
        results = tavily.search(query=query, max_results=5)
        return results.get('results', [])
    except Exception as e:
        print(f"⚠️ Search failed: {e}")
        return []


# ==================================================
# SMART SEARCH BUILDER
# ==================================================

def build_smart_search(intent_type, city, context, weather=None, preferences=None):
    """Builds contextually aware search queries."""
    current_hour = context.get('local_hour', 12)
    
    base_queries = {
        "movie": f"best movies playing now in {city} showtimes ratings",
        "food": f"best rated restaurants in {city} open now",
        "lunch": f"best lunch spots in {city} open now",
        "dinner": f"best dinner restaurants in {city} open now reservations",
        "snack": f"cafes and snack places in {city} open now",
        "bored": f"fun things to do in {city} right now today",
        "date": f"romantic restaurants and date ideas in {city}",
        "explore": f"best places to visit in {city} tourist attractions",
        "chill": f"peaceful quiet cafes and parks in {city}",
        "adventure": f"adventure activities and fun things in {city}",
        "shopping": f"best malls and shopping places in {city}",
        "nightlife": f"best bars pubs nightlife in {city}",
        "dessert": f"best dessert places ice cream cafes in {city}",
        "late_night_food": f"late night restaurants open after 10pm in {city}"
    }
    
    query = base_queries.get(intent_type, f"best places in {city}")
    
    # Weather modifications
    if weather:
        if weather.get("is_rainy"):
            query += " indoor covered"
        if weather.get("is_hot"):
            query += " air conditioned"
    
    # Preference modifications
    if preferences:
        if preferences.get("food_pref") == "vegetarian":
            query += " vegetarian veg"
        if preferences.get("budget") == "budget":
            query += " affordable cheap"
        if preferences.get("budget") == "premium":
            query += " premium upscale fine dining"
    
    return query


# ==================================================
# USER PREFERENCES (Cloud Storage)
# ==================================================

def get_user_id(request_data):
    """Generates a consistent user ID from device fingerprint."""
    fingerprint = request_data.get('fingerprint', '')
    if fingerprint:
        return hashlib.md5(fingerprint.encode()).hexdigest()[:12]
    return None


def get_user_preferences(user_id):
    """Retrieves user preferences from memory/storage."""
    return USER_PREFERENCES.get(user_id, {})


def save_user_preferences(user_id, prefs):
    """Saves user preferences."""
    if user_id:
        USER_PREFERENCES[user_id] = {**USER_PREFERENCES.get(user_id, {}), **prefs}
        return True
    return False


# ==================================================
# AI MODEL HELPER
# ==================================================

def get_working_model_url():
    """Discovers available Gemini models and selects the best one."""
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    
    try:
        response = requests.get(list_url, timeout=10)
        data = response.json()
        
        valid_models = []
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    valid_models.append(m['name'])
        
        if not valid_models:
            return f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
        
        # Prefer flash models for speed
        selected = valid_models[0]
        for m in valid_models:
            if "flash" in m.lower():
                selected = m
                break
        
        clean_name = selected.replace("models/", "")
        return f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={GEMINI_API_KEY}"
    
    except Exception as e:
        print(f"⚠️ Model discovery failed: {e}")
        return f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"


def ask_gemini(prompt, temperature=0.7):
    """Sends prompt to Gemini with JSON response."""
    url = get_working_model_url()
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 3000,
            "responseMimeType": "application/json"
        }
    }
    
    try:
        response = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=60)
        
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            raise Exception(f"AI error: {response.status_code}")
    
    except Exception as e:
        raise Exception(f"AI connection failed: {str(e)}")


# ==================================================
# LOCATION RESOLVER
# ==================================================

def resolve_location(data):
    """Resolves location from coordinates or text."""
    coords = data.get('context', {}).get('coordinates')
    loc_input = data.get('context', {}).get('location', '')
    
    # Try geocoding coordinates first
    if coords and HAS_GEOPY:
        try:
            location = geolocator.reverse(f"{coords['lat']}, {coords['lng']}", language='en', timeout=10)
            if location:
                address = location.raw.get('address', {})
                city = address.get('city') or address.get('town') or address.get('suburb') or address.get('state')
                if city:
                    return city
        except Exception as e:
            print(f"⚠️ Geocoding failed: {e}")
    
    # Use text input
    if loc_input and 'found' not in loc_input.lower() and 'location' not in loc_input.lower():
        return loc_input.strip()
    
    return "Gurugram"  # Default


# ==================================================
# CHAIN THINKING SYSTEM PROMPT
# ==================================================

CHAIN_THINKING_PROMPT = """
You are TripBuddy — an intelligent life companion that THINKS AHEAD like a thoughtful friend.

## CONTEXT
- **User Query**: {query}
- **Detected Intent**: {intent} (confidence: {confidence})
- **Activity Chain**: {chain}
- **Location**: {city}
- **Current Time**: {current_time} ({time_phase})
- **Weather**: {weather}
- **Food Timing**: {food_need}
- **Search Results**: {search_results}
- **User Preferences**: {preferences}

## YOUR TASK
Create a GROUPED plan with cards. IMPORTANT: Each card is a CATEGORY (like "Movies" or "Dinner Spots") that contains MULTIPLE options inside it.

## EXAMPLE OUTPUT (for "I want to watch a movie"):
{{
  "greeting": "Movie night! 🎬 Let's make it awesome!",
  "chain_explanation": "Since you'll finish around 9 PM, I found some dinner spots nearby too!",
  "cards": [
    {{
      "card_type": "primary",
      "emoji": "🎬",
      "title": "Movies Playing Now",
      "subtitle": "Pick your show",
      "options": [
        {{
          "name": "Dune: Part Two at PVR Ambience",
          "highlight": "IMAX experience, stunning visuals",
          "details": "Shows: 6:30 PM, 9:45 PM | ₹450",
          "tags": ["Sci-Fi", "IMAX"],
          "google_query": "PVR Ambience Mall Gurgaon"
        }},
        {{
          "name": "Fighter at Cinepolis Cyber Hub",
          "highlight": "Action-packed with Hrithik",
          "details": "Shows: 7:00 PM, 10:15 PM | ₹380",
          "tags": ["Action", "Hindi"],
          "google_query": "Cinepolis Cyber Hub Gurgaon"
        }}
      ],
      "transition": "After the movie, you'll probably want dinner..."
    }},
    {{
      "card_type": "anticipated",
      "emoji": "🍽️",
      "title": "Dinner After the Movie",
      "subtitle": "Great spots near the theater",
      "options": [
        {{
          "name": "Burma Burma",
          "highlight": "Authentic Burmese, vegetarian friendly",
          "details": "5 min walk from PVR | ₹1200 for two",
          "tags": ["Vegetarian", "Asian"],
          "google_query": "Burma Burma Cyber Hub"
        }},
        {{
          "name": "Farzi Cafe",
          "highlight": "Modern Indian, great cocktails",
          "details": "In Cyber Hub | ₹1500 for two",
          "tags": ["Indian", "Bar"],
          "google_query": "Farzi Cafe Cyber Hub Gurgaon"
        }}
      ]
    }}
  ],
  "closing": "Enjoy your movie night! 🍿"
}}

## CRITICAL RULES
1. EACH CARD = ONE CATEGORY (Movies, Dinner, Cafes, etc.)
2. EACH CARD MUST HAVE 2-3 OPTIONS INSIDE IT (not 1 per card!)
3. Use REAL place names from the search results
4. Include specific showtimes for movies, prices where possible
5. Keep max 2-3 cards total

OUTPUT ONLY VALID JSON, no markdown or explanation.
"""


# ==================================================
# MAIN ENDPOINT: /api/assist (Conversational)
# ==================================================

@app.route('/api/assist', methods=['POST'])
def assist():
    """
    Conversational endpoint that understands intent and chains activities.
    This is the new SMART endpoint.
    """
    try:
        data = request.json
        print(f"\n{'='*50}")
        print("🧠 New Assist Request")
        print(f"{'='*50}")
        
        # Extract query
        user_query = data.get('query', '').strip()
        print(f"📝 Query: {user_query}")
        
        # Resolve location
        city = resolve_location(data)
        print(f"📍 City: {city}")
        
        # Get context
        context = data.get('context', {})
        current_hour = context.get('local_hour', datetime.now().hour)
        current_time = context.get('local_time', datetime.now().strftime('%H:%M'))
        
        # Get time phase
        if current_hour < 6:
            time_phase = "late_night"
        elif current_hour < 12:
            time_phase = "morning"
        elif current_hour < 17:
            time_phase = "afternoon"
        elif current_hour < 21:
            time_phase = "evening"
        else:
            time_phase = "night"
        
        # Detect intent
        intents = detect_intent(user_query)
        primary_intent = intents[0]
        print(f"🎯 Intent: {primary_intent['intent']} ({primary_intent['confidence']:.0%})")
        
        # Get weather
        weather = get_weather(city)
        weather_str = f"{weather['temp']}°C, {weather['description']}" if weather else "Unknown"
        print(f"🌤️ Weather: {weather_str}")
        
        # Build activity chain
        chain = build_activity_chain(primary_intent['intent'], context)
        print(f"🔗 Chain: {[c['type'] for c in chain]}")
        
        # Calculate food need
        food_need = calculate_food_need(current_hour, primary_intent.get('duration', 180))
        print(f"🍽️ Food need: {food_need}")
        
        # Get user preferences
        user_id = get_user_id(data)
        preferences = get_user_preferences(user_id) if user_id else {}
        
        # Execute searches for each chain item
        search_results = {}
        for item in chain:
            query = build_smart_search(item['type'], city, context, weather, preferences)
            if tavily:
                try:
                    results = tavily.search(query=query, max_results=4)
                    search_results[item['type']] = results.get('results', [])
                except:
                    search_results[item['type']] = []
        
        # Build the master prompt
        prompt = CHAIN_THINKING_PROMPT.format(
            query=user_query,
            intent=primary_intent['intent'],
            confidence=f"{primary_intent['confidence']:.0%}",
            chain=json.dumps([c['type'] for c in chain]),
            city=city,
            current_time=current_time,
            time_phase=time_phase,
            weather=weather_str,
            food_need=food_need or "None needed",
            search_results=json.dumps(search_results, indent=2)[:3000],  # Limit size
            preferences=json.dumps(preferences) if preferences else "None saved"
        )
        
        # Get AI response
        print("🤖 Generating smart response...")
        raw_response = ask_gemini(prompt)
        
        # Parse JSON
        response = json.loads(raw_response.strip())
        
        # Add metadata
        response['meta'] = {
            'intent': primary_intent['intent'],
            'confidence': primary_intent['confidence'],
            'city': city,
            'weather': weather,
            'chain': [c['type'] for c in chain]
        }
        
        print("✅ Smart response generated!")
        return jsonify(response)
    
    except json.JSONDecodeError as e:
        print(f"❌ JSON Parse Error: {e}")
        return jsonify({
            "greeting": "Let me help you with that!",
            "chain_explanation": "I'm thinking about what you need...",
            "cards": [{
                "card_type": "primary",
                "emoji": "🔄",
                "title": "Let me try again",
                "subtitle": "Something went sideways",
                "options": [{
                    "name": "Tap to retry",
                    "highlight": "I'll give it another shot",
                    "google_query": f"things to do in {city}"
                }]
            }],
            "closing": "Hit retry and I'll figure this out! 🚀"
        })
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({
            "greeting": "Oops, small hiccup!",
            "cards": [{
                "card_type": "primary",
                "emoji": "⚡",
                "title": "Quick Recovery",
                "subtitle": str(e)[:100],
                "options": []
            }],
            "closing": "Try again in a moment!"
        }), 500


# ==================================================
# NEW ENDPOINT: /api/clarify (Multi-turn Questions)
# ==================================================

@app.route('/api/clarify', methods=['POST'])
def clarify():
    """
    Handles multi-turn conversations with clarifying questions.
    Returns next question OR final plan when all questions answered.
    """
    try:
        data = request.json
        print(f"\n{'='*50}")
        print("💬 Clarify Request")
        print(f"{'='*50}")
        
        # Get conversation state
        intent = data.get('intent', 'explore')
        answers = data.get('answers', {})
        original_query = data.get('original_query', '')
        
        print(f"🎯 Intent: {intent}")
        print(f"📝 Answers so far: {answers}")
        
        # Check if conversation is complete
        next_question = get_next_question(intent, answers)
        
        if next_question:
            # Return the next question
            print(f"❓ Next question: {next_question['id']}")
            return jsonify({
                "type": "clarification",
                "question": next_question,
                "intent": intent,
                "progress": {
                    "answered": len(answers),
                    "total": len(CLARIFYING_QUESTIONS.get(intent, {}).get("questions", []))
                }
            })
        
        # All questions answered - execute the plan!
        print("✅ All questions answered - generating plan")
        
        # Build enhanced context with answers
        enhanced_data = {
            **data,
            "query": original_query,
            "user_answers": answers
        }
        
        # For day_plan, use special handler
        if intent == "day_plan":
            return execute_day_plan(enhanced_data)
        
        # For other intents, use enhanced assist
        return execute_smart_plan(intent, answers, enhanced_data)
    
    except Exception as e:
        print(f"❌ Clarify Error: {e}")
        return jsonify({
            "type": "error",
            "message": str(e)[:100]
        }), 500


def execute_smart_plan(intent, answers, data):
    """Executes the final plan based on intent and user answers."""
    city = resolve_location(data)
    context = data.get('context', {})
    current_hour = context.get('local_hour', datetime.now().hour)
    
    # Build search queries based on answers
    food_type = answers.get("food_type", "any")
    food_timing = answers.get("food_timing", "none")
    budget = answers.get("budget", "mid")
    
    # Search modifiers
    food_mods = {
        "quick": "fast food quick service",
        "nice": "best rated restaurant dine in",
        "street": "popular street food local famous",
        "cafe": "cafe coffee snacks"
    }
    
    budget_mods = {
        "budget": "affordable cheap",
        "mid": "",
        "premium": "premium upscale fine dining"
    }
    
    # Build searches
    search_results = {}
    
    # Get current date for fresh movie results
    current_date = datetime.now().strftime("%B %Y")  # e.g., "January 2026"
    
    # Primary intent search
    if tavily:
        try:
            if intent == "movie":
                movie_pref = answers.get("movie_pref", "any")
                # Search for CURRENT movies with today's date
                movie_query = f"movies releasing and playing in {city} {current_date} new releases showtimes PVR INOX {movie_pref if movie_pref != 'any' else ''}"
                print(f"🎬 Movie search: {movie_query}")
                results = tavily.search(query=movie_query, max_results=5)
                search_results["movie"] = results.get('results', [])
            
            # Food search - look for places NEAR THEATERS (on the way)
            if food_timing in ["before", "after"]:
                food_mod = food_mods.get(food_type, "")
                budget_mod = budget_mods.get(budget, "")
                # Search for food near malls/theaters for "on the way" experience
                food_query = f"{food_mod} restaurants near PVR INOX Cyber Hub {city} {budget_mod}"
                print(f"🍽️ Food search: {food_query}")
                results = tavily.search(query=food_query, max_results=4)
                search_results["food"] = results.get('results', [])
        except Exception as e:
            print(f"⚠️ Search error: {e}")
    
    # Build prompt for Gemini with EXPLICIT structure
    smart_prompt = f"""
You are TripBuddy creating a plan for someone in {city}.

USER REQUEST: "{data.get('query', '')}"
USER PREFERENCES: {json.dumps(answers)}
SEARCH DATA: {json.dumps(search_results)[:2500]}
CURRENT DATE: {current_date}

CREATE A PLAN with this structure:
1. FIRST: Show MOVIES card (what they'll watch)
2. THEN: Show FOOD card (places ON THE WAY to the theater)

EXACT JSON FORMAT:
{{
  "greeting": "Friendly opening about movie night",
  "chain_explanation": "Since you want to eat {answers.get('food_timing', 'before')}, I found {answers.get('food_type', 'good')} food spots near the theaters!",
  "cards": [
    {{
      "card_type": "primary",
      "emoji": "🎬",
      "title": "Movies Playing Now",
      "subtitle": "New releases in {city}",
      "options": [
        {{"name": "Movie Name at Theater Name", "highlight": "Why see it", "details": "Showtimes | ₹Price", "tags": ["Genre"], "google_query": "theater full address"}}
      ]
    }},
    {{
      "card_type": "anticipated", 
      "emoji": "🍽️",
      "title": "{answers.get('food_type', 'Food').title() if answers.get('food_type') else 'Food'} Spots on the Way",
      "subtitle": "Grab a bite {'before' if answers.get('food_timing') == 'before' else 'after'} the show",
      "options": [
        {{"name": "Restaurant Name", "highlight": "Why it's good", "details": "Near [Theater] | ₹Price for two", "tags": ["Cuisine"], "google_query": "restaurant name city"}}
      ]
    }}
  ],
  "closing": "Enjoy your movie night! 🍿"
}}

CRITICAL RULES:
1. ONLY use movies from the search results - DO NOT make up movie names
2. Food should be described as "near" or "on the way to" the theater
3. Include 2-3 options per card
4. Each option must have name, highlight, details, tags, google_query

OUTPUT ONLY VALID JSON.
"""
    
    try:
        raw_response = ask_gemini(smart_prompt)
        response = json.loads(raw_response.strip())
        response['type'] = 'final_plan'
        response['meta'] = {'intent': intent, 'answers': answers}
        return jsonify(response)
    except:
        return jsonify({
            "type": "final_plan",
            "greeting": "Here's your personalized plan!",
            "cards": [],
            "closing": "Enjoy!"
        })


def execute_day_plan(data):
    """Generates a full-day itinerary based on user preferences."""
    city = resolve_location(data)
    answers = data.get('user_answers', {})
    context = data.get('context', {})
    
    start_time = answers.get("start_time", "morning")
    vibe = answers.get("vibe", "balanced")
    must_include = answers.get("must_include", [])
    budget = answers.get("budget", "mid")
    
    # Time mapping
    start_hours = {"morning": 9, "noon": 12, "evening": 17}
    start_hour = start_hours.get(start_time, 9)
    
    # Search for activities
    search_results = {}
    if tavily:
        try:
            # Base search
            vibe_query = f"things to do in {city} {vibe}"
            results = tavily.search(query=vibe_query, max_results=5)
            search_results["activities"] = results.get('results', [])
            
            # Food search
            food_query = f"best restaurants in {city} {budget}"
            results = tavily.search(query=food_query, max_results=4)
            search_results["food"] = results.get('results', [])
            
            # Must-include searches
            if "movie" in must_include:
                results = tavily.search(query=f"movies playing in {city} today showtimes", max_results=3)
                search_results["movie"] = results.get('results', [])
            if "cafe" in must_include:
                results = tavily.search(query=f"best cafes in {city}", max_results=3)
                search_results["cafe"] = results.get('results', [])
        except Exception as e:
            print(f"⚠️ Day plan search error: {e}")
    
    # Build the day plan prompt
    day_prompt = f"""
You are TripBuddy creating a FULL DAY PLAN for someone in {city}.

Their preferences:
- Start time: {start_time} (~{start_hour}:00)
- Vibe: {vibe}
- Must include: {must_include if must_include else "flexible"}
- Budget: {budget}

Search results to use:
{json.dumps(search_results)[:2500]}

Create a timeline for their day. Output JSON:
{{
  "greeting": "Excited greeting about their day!",
  "day_title": "Title for their day (e.g., 'Your Chill Saturday')",
  "timeline": [
    {{
      "time": "10:00 AM",
      "emoji": "☕",
      "activity": "Activity name",
      "place": "Specific place name",
      "details": "Why this fits their vibe",
      "google_query": "search for maps"
    }}
  ],
  "total_budget_estimate": "₹X - ₹Y",
  "tips": ["Any helpful tips"],
  "closing": "Friendly send-off"
}}

Rules:
- Use REAL places from search results
- Space activities 2-3 hours apart
- Include lunch and dinner if appropriate
- Match the {vibe} vibe exactly
"""
    
    try:
        raw_response = ask_gemini(day_prompt)
        response = json.loads(raw_response.strip())
        response['type'] = 'day_plan'
        return jsonify(response)
    except Exception as e:
        print(f"❌ Day plan generation error: {e}")
        return jsonify({
            "type": "day_plan",
            "greeting": "Here's your day!",
            "timeline": [],
            "closing": "Have a great time!"
        })


# ==================================================
# LEGACY ENDPOINT: /api/plan (Keep for compatibility)
# ==================================================

@app.route('/api/plan', methods=['POST'])
def plan_trip():
    """Legacy endpoint - redirects to new smart assist."""
    data = request.json
    
    # Convert legacy format to new format
    plan_type = data.get('plan_type', 'NOW')
    traveler = data.get('traveler', {})
    
    # Build a query from the old format
    occasion = traveler.get('occasion', 'exploration')
    group = traveler.get('group', 'solo')
    
    query_map = {
        'celebration': "I want to celebrate something special",
        'escape': "I need to relax and unwind",
        'exploration': "I want to explore and discover new places",
        'romance': "Plan a romantic date",
        'worktrip': "I'm on a work trip and want to explore after hours"
    }
    
    synthetic_query = query_map.get(occasion, "Show me what's good around here")
    
    # Add to data and call new endpoint
    data['query'] = synthetic_query
    
    # Forward to assist
    with app.test_request_context('/api/assist', method='POST', json=data):
        return assist()


# ==================================================
# USER PREFERENCES ENDPOINTS
# ==================================================

@app.route('/api/preferences', methods=['GET'])
def get_prefs():
    """Get user preferences."""
    user_id = request.args.get('user_id')
    if user_id:
        return jsonify(get_user_preferences(user_id))
    return jsonify({})


@app.route('/api/preferences', methods=['POST'])
def save_prefs():
    """Save user preferences."""
    data = request.json
    user_id = data.get('user_id') or get_user_id(data)
    prefs = data.get('preferences', {})
    
    if save_user_preferences(user_id, prefs):
        return jsonify({"status": "saved", "user_id": user_id})
    return jsonify({"status": "error"}), 400


# ==================================================
# HEALTH CHECK
# ==================================================

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "Travel Buddy v3.0 - Intelligent Life Companion",
        "features": [
            "intent_detection",
            "chain_thinking", 
            "weather_aware",
            "food_timing",
            "user_preferences"
        ],
        "endpoints": {
            "/api/assist": "Conversational smart assistant (NEW)",
            "/api/plan": "Legacy trip planner (compatible)",
            "/api/preferences": "User preferences storage"
        }
    }), 200


# ==================================================
# STARTUP
# ==================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n🚀 Travel Buddy v3.0 - Intelligent Life Companion")
    print(f"📍 Starting on port {port}")
    print("=" * 50)
    print("Features enabled:")
    print(f"  ✅ Intent Detection ({len(INTENT_PATTERNS)} patterns)")
    print(f"  ✅ Chain Thinking")
    print(f"  {'✅' if OPENWEATHER_API_KEY else '⚠️'} Weather API")
    print(f"  {'✅' if tavily else '⚠️'} Tavily Search")
    print(f"  ✅ User Preferences")
    print("=" * 50)
    app.run(host='0.0.0.0', port=port, debug=False)

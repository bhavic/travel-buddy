"""
Travel Buddy Pro v3.0 - Intelligent Life Companion
An AI assistant that thinks like a friend, anticipates needs, and chains activities.
"""

import os
import json
import requests
import hashlib
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient

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
CORS(app)

# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY")  # Free tier
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")  # Free tier

# In-memory user preferences store (persists during server lifetime)
# For production, use Redis or a database
USER_PREFERENCES = {}

tavily = None
if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)
    print("✅ Tavily Active - Real-time search enabled")

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
# MOVIE SEARCH (TMDB + Tavily)
# ==================================================

def get_now_playing_movies(city):
    """Gets currently playing movies using web search."""
    if not tavily:
        return []
    
    try:
        query = f"movies now playing in {city} today showtimes PVR INOX Cinepolis"
        results = tavily.search(query=query, max_results=5)
        return results.get('results', [])
    except Exception as e:
        print(f"⚠️ Movie search failed: {e}")
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

## YOUR SUPERPOWER: CHAIN THINKING
When someone says "I want to watch a movie", you don't just find movies.
You think: Movie → What time? → Will they be hungry after? → Where to eat? → How to get there?

## CONTEXT PROVIDED
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
Create a SMART PLAN that anticipates their needs. Structure as "cards" that chain logically.

## OUTPUT FORMAT (Strict JSON)
{{
  "greeting": "Warm, casual opening that shows you understood their intent",
  "chain_explanation": "One sentence explaining your thinking (e.g., 'Since you'll finish around 9 PM, I found dinner spots too!')",
  "cards": [
    {{
      "card_type": "primary|anticipated|bonus",
      "emoji": "🎬",
      "title": "Main heading",
      "subtitle": "Brief context",
      "options": [
        {{
          "name": "Specific place name",
          "highlight": "What makes it special",
          "details": "Time/distance/price info",
          "tags": ["Tag1", "Tag2"],
          "google_query": "exact search for Google Maps"
        }}
      ],
      "transition": "How this connects to the next card (only if not last)"
    }}
  ],
  "quick_actions": [
    {{
      "label": "Button text",
      "action": "navigate|call|save|more",
      "data": "relevant data for action"
    }}
  ],
  "closing": "Friendly sign-off with anticipation",
  "follow_up_question": "Optional question if you need more info (null if not needed)"
}}

## RULES
1. ALWAYS use REAL place names — never generic like "Local Cafe"
2. If chain includes food, ALWAYS include restaurant options
3. Keep it conversational, not formal
4. Show your chain thinking in chain_explanation
5. Max 3 options per card to avoid overwhelm
6. Include travel time between spots if relevant
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

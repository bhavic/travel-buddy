"""
Travel Buddy - Logistics Engine
Optimized for Real-Time Data, Movies, and Sequential Planning.
"""

import os
import json
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
# CORS setup is crucial for Shopify to talk to Render
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "allow_headers": ["Content-Type", "Accept"],
    "methods": ["GET", "POST", "OPTIONS"]
}})

# Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Endpoint for Gemini 2.0 Flash
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# ==========================================
# THE "LOGISTICS ENGINE" PROMPT
# ==========================================
SYSTEM_PROMPT = """
You are a "Logistics Expert & Travel Companion".
Your job is to turn vague requests into EXACT, step-by-step plans with REAL data.

CRITICAL RULES:

1. STRICT TIME AWARENESS:
   - User Current Time: {current_time}
   - User Timezone: {timezone}
   - You MUST plan activities starting AFTER the current time.
   - If it is 11 PM, do NOT plan a movie for 9 PM. Plan for 11:30 PM or next day.

2. DISTANCE & TRAVEL TIME:
   - User Coordinates: {user_coords}
   - You MUST estimate travel time between stops based on coordinates.
   - Assume City Traffic Speed: ~25 km/h.
   - Add a step like "🚗 Travel from Home to Theater (15 mins)" if moving between locations.

3. REAL DATA - MOVIES (TOP PRIORITY):
   - If the query involves "movie", "show", or "cinema":
   - You MUST use the search tool to find: "Movies showing in {location} on {date}".
   - Include SPECIFIC showtimes: "7:30 PM at PVR", "10:00 AM at INOX".
   - DO NOT just say "Check BookMyShow". Give the real time or say "None found nearby".

4. FOOD & PARKING:
   - If user selected "Car", include parking info: "Basement parking free with ticket".
   - Include specific restaurant names and cuisine type.

5. OUTPUT FORMAT (JSON ONLY):
   - If this is a Plan/Day Plan/Wizard query, return type "day_plan".
   - If this is a simple "Suggest X" query, return type "itinerary" with cards.

JSON SCHEMA FOR "day_plan":
{
  "type": "day_plan",
  "greeting": "Friendly sentence",
  "day_title": "Plan Name",
  "timeline": [
    {
      "time": "HH:MM AM/PM",
      "emoji": "📍",
      "activity": "Name of Activity",
      "place": "Specific Place Name",
      "details": "Address, Price, Parking, Duration",
      "google_query": "Exact name for Maps",
      "travel_time_to_next": "X mins"
    }
  ],
  "total_budget_estimate": "₹X,XXX",
  "tips": ["Tip 1"],
  "closing": "Sign off"
}

JSON SCHEMA FOR "itinerary" (Simple list):
{
  "type": "itinerary",
  "greeting": "...",
  "cards": [
    {
      "title": "...",
      "emoji": "📍",
      "options": [{"name": "Place", "details": "..."}]
    }
  ]
}
"""

def call_gemini(user_query: str, context: dict, preferences: dict) -> dict:
    """Calls Gemini with forced Search Grounding."""
    
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not configured"}
    
    # 1. Prepare Context
    location = context.get('location') or 'Unknown'
    coords = context.get('coordinates') or {}
    local_time = context.get('local_time') or datetime.now().strftime('%H:%M')
    local_hour = context.get('local_hour') or datetime.now().hour
    timezone = context.get('timezone') or 'Asia/Kolkata'
    
    # Format Coordinates for readability
    coord_str = f"{coords.get('lat', 'N/A')}, {coords.get('lng', 'N/A')}"
    
    # 2. Date Logic (Crucial for Movies)
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    date_str = today.strftime('%A, %B %d')
    tomorrow_str = tomorrow.strftime('%A, %B %d')

    # 3. Detect Intent (Movie vs General)
    is_movie_intent = any(kw in user_query.lower() for kw in ['movie', 'film', 'show', 'cinema', 'watch'])
    
    # 4. Inject Specific Instructions for Movies
    search_hint = ""
    if is_movie_intent:
        # Force AI to search for TODAY's or TOMORROW's movies
        if local_hour < 18: # Before 6 PM, assume today
            search_hint = f"CRITICAL: Search for 'Movies showing in {location} today ({date_str})'. Find specific showtimes."
        else:
            search_hint = f"CRITICAL: Search for 'Movies showing in {location} tomorrow ({tomorrow_str})' as it is late."
    
    # 5. Build the Final Context String
    context_block = f"""
USER CONTEXT:
- Location: {location}
- Coordinates: {coord_str}
- Current Time: {local_time}
- Timezone: {timezone}
- Date: {date_str}
- Preferences: {json.dumps(preferences)}

USER REQUEST: {user_query}

{search_hint}

Use Google Search to find REAL information. If querying locations, use the coordinates to find the NEAREST options.
"""

    # 6. API Payload
    payload = {
        "contents": [{"role": "user", "parts": [{"text": context_block}]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "tools": [{"google_search": {}}], # Enable Grounding
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096,
            "responseMimeType": "application/json" # Force JSON output mode
        }
    }
    
    try:
        response = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        
        if response.status_code != 200:
            print(f"❌ Gemini Error: {response.status_code} - {response.text}")
            return create_fallback_response(user_query, location)
        
        result = response.json()
        
        # Deep extraction of text
        try:
            text = result['candidates'][0]['content']['parts'][0]['text']
        except (KeyError, IndexError):
            print("❌ Invalid response structure from Gemini")
            return create_fallback_response(user_query, location)

        # Parse JSON
        try:
            # Sometimes response adds markdown, strip it
            text = text.replace('```json', '').replace('```', '').strip()
            return json.loads(text)
        except json.JSONDecodeError:
            # Fallback regex parsing (robust)
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            else:
                print("❌ Could not parse JSON from response")
                return create_fallback_response(user_query, location)

    except Exception as e:
        print(f"❌ Server Error: {e}")
        import traceback
        traceback.print_exc()
        return create_fallback_response(user_query, location)

def create_fallback_response(query: str, location: str) -> dict:
    """Smart fallback if AI fails."""
    return {
        "type": "itinerary",
        "greeting": "I'm having a little trouble connecting right now.",
        "cards": [
            {
                "title": "Search on Google Maps",
                "subtitle": f"Find '{query}' near {location}",
                "card_type": "primary",
                "options": [
                    {
                        "name": f"Search: {query}",
                        "details": "Tap to open Google Maps",
                        "google_query": f"{query} in {location}"
                    }
                ]
            }
        ],
        "closing": "Please try again in a moment!"
    }

# ==========================================
# ROUTES
# ==========================================

@app.route('/api/assist', methods=['POST', 'OPTIONS'])
def assist():
    if request.method == 'OPTIONS':
        return '', 204 # CORS Preflight
    
    try:
        data = request.json or {}
        query = data.get('query', '')
        context = data.get('context', {})
        preferences = data.get('preferences', {})
        
        print(f"📥 Incoming Query: {query}")
        
        result = call_gemini(query, context, preferences)
        
        # Ensure type is set
        if 'type' not in result:
            result['type'] = 'itinerary'
            
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ API Route Error: {e}")
        return jsonify({"type": "error", "greeting": "Something went wrong."}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "service": "Travel Buddy V3"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

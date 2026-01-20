import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient
from datetime import datetime

# GEOPY SETUP (The Fix for San Francisco)
try:
    from geopy.geocoders import Nominatim
    geolocator = Nominatim(user_agent="travel_buddy_app")
    HAS_GEOPY = True
except ImportError:
    HAS_GEOPY = False
    print("WARNING: geopy not installed.")

app = Flask(__name__)
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

tavily = None
if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)

# --- 1. FAST CONNECTION FUNCTION ---
def ask_google(prompt):
    # Try Flash first (Fastest), then Pro (Backup)
    endpoints = [
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}",
    ]
    
    last_err = ""
    for url in endpoints:
        try:
            payload = { "contents": [{ "parts": [{"text": prompt}] }], "generationConfig": { "temperature": 0.4 } }
            # 60s timeout for python request
            response = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=60)
            
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
            else:
                last_err = response.text
        except Exception as e:
            last_err = str(e)
            
    raise Exception(f"Google AI Unreachable: {last_err}")

# --- 2. INTELLIGENT PROMPT ---
SYSTEM_PROMPT = """
You are "TripBuddy", a local expert.
OBJECTIVE: Plan a specific itinerary.

RULES:
1. **SPECIFICITY:** Name real, verifiable places.
2. **FALLBACK:** If search data is empty, USE YOUR INTERNAL KNOWLEDGE of the city.
3. **JSON ONLY:** Output pure JSON.

OUTPUT FORMAT:
{
  "meta": { "summary": "Vibe check summary.", "weather_advice": "Wear sunscreen." },
  "timeline": [
    {
      "time_slot": "10:00 - 11:30",
      "activity_type": "ACTIVITY",
      "title": "Specific Place Name",
      "address": "Area, City",
      "description": "Why it's good. Rating: 4.5/5",
      "tags": ["Cafe", "Outdoor", "$$"],
      "open_status": "Open until 11 PM",
      "estimated_travel_time_next": "20 mins",
      "google_query": "Specific Place Name City"
    }
  ]
}
"""

@app.route('/', methods=['GET'])
def health_check():
    return "Travel Buddy V5 (Geo-Fix) is Running!", 200

@app.route('/api/plan', methods=['POST'])
def plan_trip():
    try:
        data = request.json
        print("Received Data:", data)

        # --- 3. ROBUST LOCATION LOGIC (THE FIX) ---
        trip_type = data.get('plan_type', 'NOW')
        loc_input = data['context'].get('location', '')
        dest_input = data['context'].get('destination', '')
        coords = data['context'].get('coordinates')

        target_city = "Gurugram" # Final fallback

        # If User provided specific destination for a TRIP
        if trip_type == 'TRIP' and dest_input and len(dest_input) > 2:
            target_city = dest_input
        
        # If User provided coordinates (Detect Me)
        elif coords and HAS_GEOPY:
            try:
                print(f"📍 Reverse Geocoding: {coords['lat']}, {coords['lng']}")
                location = geolocator.reverse(f"{coords['lat']}, {coords['lng']}", language='en')
                address = location.raw['address']
                # Try to get city, then town, then state
                target_city = address.get('city') or address.get('town') or address.get('state') or "Gurugram"
                print(f"✅ Detected City: {target_city}")
            except Exception as e:
                print(f"⚠️ Geocoding failed: {e}")
                # If geocoding fails but input has text (even 'Location Found'), default to Gurugram to be safe
                target_city = "Gurugram"
        
        # If user typed a city manually
        elif loc_input and "Location" not in loc_input:
            target_city = loc_input

        print(f"🎯 FINAL TARGET CITY: {target_city}")

        # --- 4. SEARCH ---
        search_context = ""
        if tavily:
            try:
                q = f"Top rated tourist attractions and restaurants in {target_city} for {data.get('users', {}).get('vibe', 'general')} vibe"
                print(f"🔎 Searching: {q}")
                res = tavily.search(query=q, max_results=4) 
                if res.get('results'):
                    search_context = json.dumps(res['results'])
            except Exception as e:
                print(f"Search Error: {e}")

        # --- 5. GENERATE ---
        full_prompt = f"""
        {SYSTEM_PROMPT}
        
        CONTEXT:
        - Plan: {trip_type}
        - City: {target_city}
        - Vibe: {json.dumps(data.get('users'))}
        
        AVAILABLE PLACES:
        {search_context if search_context else "Search failed. Use internal knowledge for " + target_city}
        """
        
        raw_response = ask_google(full_prompt)
        clean_json = raw_response.replace("```json", "").replace("```", "").strip()
        
        # Validation Loop
        if "Local Eatery" in clean_json or "San Francisco" in clean_json:
            full_prompt += f"\n\nERROR: You used generic names or the wrong city. REWRITE for {target_city}."
            raw_response = ask_google(full_prompt)
            clean_json = raw_response.replace("```json", "").replace("```", "").strip()

        return jsonify(json.loads(clean_json))

    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

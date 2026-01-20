import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient

# --- 1. GEOPY SETUP (Finds the City) ---
try:
    from geopy.geocoders import Nominatim
    geolocator = Nominatim(user_agent="travel_buddy_app")
    HAS_GEOPY = True
    print("*** GEOPY IS ACTIVE ***")
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

# --- 2. DYNAMIC MODEL DISCOVERY (Fixes "AI Unreachable") ---
def get_working_model_url():
    """Asks Google for a list of models and picks the best valid one."""
    print("🔎 Asking Google for available models...")
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    
    try:
        response = requests.get(list_url)
        data = response.json()
        
        valid_models = []
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    valid_models.append(m['name'])
        
        if not valid_models:
            print("⚠️ No models found in list. Trying fallback.")
            return f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
            
        # Pick the best one (Flash > Pro > Standard)
        selected = valid_models[0]
        for m in valid_models:
            if "flash" in m or "1.5" in m:
                selected = m
                break
                
        clean_name = selected.replace("models/", "")
        print(f"✅ Selected Model: {clean_name}")
        return f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={GEMINI_API_KEY}"

    except Exception as e:
        print(f"⚠️ Discovery Error: {e}. Using fallback.")
        return f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"

def ask_google(prompt):
    # 1. Get the correct URL dynamically
    url = get_working_model_url()
    
    # 2. Send Request
    payload = { "contents": [{ "parts": [{"text": prompt}] }], "generationConfig": { "temperature": 0.5 } }
    
    # We allow 60s timeout so Render doesn't kill it easily
    response = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=60)
    
    if response.status_code == 200:
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    else:
        # Print the EXACT error from Google to the logs
        print(f"❌ Google API Error: {response.text}")
        raise Exception(f"Google Error {response.status_code}: {response.text}")

# --- 3. SYSTEM PROMPT ---
SYSTEM_PROMPT = """
You are "TripBuddy", a local expert.
OBJECTIVE: Plan a specific itinerary.

RULES:
1. **SPECIFICITY:** Name real, verifiable places.
2. **FALLBACK:** If search data is empty, USE YOUR INTERNAL KNOWLEDGE.
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
    return "Travel Buddy Final (Auto-Discovery + Geopy) is Running!", 200

@app.route('/api/plan', methods=['POST'])
def plan_trip():
    try:
        data = request.json
        print("Received Data:", data)

        # --- 4. ROBUST LOCATION LOGIC ---
        trip_type = data.get('plan_type', 'NOW')
        loc_input = data['context'].get('location', '')
        dest_input = data['context'].get('destination', '')
        coords = data['context'].get('coordinates')

        target_city = "Gurugram" # Ultimate Fallback

        if trip_type == 'TRIP' and dest_input and len(dest_input) > 2:
            target_city = dest_input
        
        elif coords and HAS_GEOPY:
            try:
                # Use coordinates to find the real city
                print(f"📍 Geocoding Coords: {coords['lat']}, {coords['lng']}")
                location = geolocator.reverse(f"{coords['lat']}, {coords['lng']}", language='en')
                address = location.raw['address']
                target_city = address.get('city') or address.get('town') or address.get('state') or "Gurugram"
                print(f"✅ Detected City: {target_city}")
            except Exception as e:
                print(f"⚠️ Geocoding failed: {e}")
                target_city = "Gurugram"

        elif loc_input:
            if "Location" in loc_input or "Found" in loc_input:
                 target_city = "Gurugram"
            else:
                 target_city = loc_input

        print(f"🎯 FINAL TARGET CITY: {target_city}")

        # --- 5. SEARCH ---
        search_context = ""
        if tavily:
            try:
                q = f"Top rated tourist attractions and restaurants in {target_city} for {data.get('users', {}).get('vibe', 'general')} vibe"
                print(f"🔎 Searching: {q}")
                res = tavily.search(query=q, max_results=4) 
                if res.get('results'):
                    search_context = json.dumps(res['results'])
            except: pass

        # --- 6. GENERATE ---
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
        
        # Double Check for San Francisco bug
        if "San Francisco" in clean_json:
             full_prompt += f"\n\nERROR: You generated a plan for the wrong city. REWRITE for {target_city}."
             raw_response = ask_google(full_prompt)
             clean_json = raw_response.replace("```json", "").replace("```", "").strip()

        return jsonify(json.loads(clean_json))

    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

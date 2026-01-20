import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient
from datetime import datetime

# TRY TO IMPORT GEOPY
try:
    from geopy.distance import geodesic
    HAS_GEOPY = True
except ImportError:
    HAS_GEOPY = False
    print("WARNING: 'geopy' not installed.")

app = Flask(__name__)
CORS(app)

# KEYS
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

# SETUP TAVILY
tavily = None
if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)

# 1. AUTO-DISCOVERY FUNCTION
def get_live_model():
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
        response = requests.get(url)
        data = response.json()
        preferred = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.0-pro", "gemini-pro"]
        available = []
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    available.append(m['name'].replace("models/", ""))
        for p in preferred:
            if p in available: return p
        if available: return available[0]
    except: pass
    return "gemini-pro"

# 2. SYSTEM PROMPT
SYSTEM_PROMPT = """
You are a "Logistics Expert Travel Buddy".
OBJECTIVE: Create a STRICT SEQUENTIAL TIMELINE.

INPUT CONTEXT:
- Plan Type: {plan_type}
- Location: {search_city}
- Date: {current_date}
- Distance Context: {distance_hint}

INSTRUCTIONS:
1. If Plan Type is "TRIP": Plan a 3-day itinerary.
2. If Plan Type is "NOW": Plan the next 4-6 hours.
3. If Plan Type is "TOMORROW": Plan a full day (9 AM to 10 PM).

CRITICAL RULES:
- **Sequence:** List stops in order (Start -> Food -> Activity -> Food -> End).
- **Interleave Meals:** Do not just list attractions. Insert lunch/dinner.
- **Entertainment:** If search results show NO movies/events, explicitly state in description: "No live cinema events found, opted for [Alternative]".
- **Travel Time:** Use the "Distance Context" provided to estimate realistic travel times.

OUTPUT JSON FORMAT:
{
  "meta": { 
    "summary": "One sentence overview.", 
    "weather_advice": "General advice (e.g., carry an umbrella)." 
  },
  "timeline": [
    {
      "time_slot": "14:00 - 15:30",
      "activity_type": "FOOD | ACTIVITY | TRAVEL",
      "title": "Name of Place",
      "address": "Area, City",
      "description": "Why this fits + details.",
      "estimated_travel_time_next": "25 mins by car",
      "google_query": "Place Name City"
    }
  ]
}
"""

@app.route('/', methods=['GET'])
def health_check():
    return "Travel Buddy V2 is Running!", 200

@app.route('/api/plan', methods=['POST'])
def plan_trip():
    data = request.json
    print("Received Data:", data)

    # --- 1. DETERMINE SEARCH LOCATION ---
    trip_type = data.get('plan_type', 'NOW')
    current_location = data['context'].get('location', 'Unknown')
    search_city = data['context'].get('destination', current_location)
    
    if trip_type == 'TRIP' and search_city == current_location:
         # Fallback if specific destination not set, try user notes or default
        search_city = data['context'].get('user_notes', current_location)

    # Coordinates
    start_coords = None
    coords_data = data['context'].get('coordinates')
    if coords_data:
        start_coords = (coords_data['lat'], coords_data['lng'])

    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # --- 2. SEARCH ---
    search_results_text = ""
    first_result_coords = None

    if tavily:
        try:
            # A. Movies
            movie_query = f"Movies and events showing in {search_city} on {today_str}"
            movies = tavily.search(query=movie_query, max_results=3)
            
            # B. Places
            place_query = f"Best tourist spots, restaurants, and things to do in {search_city}"
            places = tavily.search(query=place_query, max_results=8)
            
            combined_results = movies.get('results', []) + places.get('results', [])
            search_results_text = json.dumps(combined_results)

            # Try to get coords from first result for distance calc
            if places.get('results'):
                # Tavily sometimes doesn't give coords, but if it did, we'd use them
                pass 

        except Exception as e:
            print(f"Search Error: {e}")
            search_results_text = "No external search data available."

    # --- 3. CALCULATE DISTANCE (The Geopy Fix) ---
    distance_hint = "Calculate travel times based on city traffic."
    
    # We don't have the destination coordinates easily unless we Geocode the city name.
    # For now, we rely on the AI knowing the distance between 'start_coords' and 'search_city'.
    if start_coords and search_city != current_location:
         distance_hint = f"The user is currently at {start_coords}. They are traveling to {search_city}. Calculate travel time between these two points first."

    # --- 4. GEMINI GENERATION ---
    try:
        model_name = get_live_model()
        
        formatted_prompt = SYSTEM_PROMPT.format(
            plan_type=trip_type,
            search_city=search_city,
            current_date=today_str,
            distance_hint=distance_hint
        )
        
        full_prompt = f"""
        {formatted_prompt}
        
        USER INPUT DATA: {json.dumps(data)}
        
        EXTERNAL SEARCH RESULTS:
        {search_results_text}
        """
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        headers = {'Content-Type': 'application/json'}
        payload = { "contents": [{ "parts": [{"text": full_prompt}] }] }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            print(f"GOOGLE ERROR: {response.text}")
            return jsonify({
                "meta": {"summary": "AI overloaded. Please try again."}, 
                "timeline": []
            })

        json_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        clean_json = json_text.replace("```json", "").replace("```", "").strip()
        
        return jsonify(json.loads(clean_json))
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

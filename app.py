import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient
from datetime import datetime

# TRY TO IMPORT GEOPY FOR REAL DISTANCE CALCULATION
# You MUST run: pip install geopy
try:
    from geopy.distance import geodesic
    HAS_GEOPY = True
except ImportError:
    HAS_GEOPY = False
    print("WARNING: 'geopy' not installed. Travel times will be estimates.")

app = Flask(__name__)
CORS(app)

# KEYS
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

# SETUP TAVILY
tavily = None
if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)

# 1. AUTO-DISCOVERY FUNCTION (Kept your logic, it's good)
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

# 2. NEW SYSTEM PROMPT (Sequential Logic)
SYSTEM_PROMPT = """
You are a "Logistics Expert Travel Buddy".
OBJECTIVE: Create a STRICT SEQUENTIAL TIMELINE.

INPUT CONTEXT:
- Plan Type: {plan_type}
- Location: {search_city}
- Date: {current_date}

INSTRUCTIONS:
1. If Plan Type is "TRIP": Plan a 3-day itinerary for the destination.
2. If Plan Type is "NOW": Plan the next 4-6 hours starting from current time.
3. If Plan Type is "TOMORROW": Plan a full day (9 AM to 10 PM).

CRITICAL RULES:
- **Sequence:** You MUST output a list of stops in order (Start -> Food -> Activity -> Food -> End).
- **Interleave Meals:** Do not just list attractions. Insert lunch/dinner stops.
- **Travel Time:** If "Real Calculation" data is provided, use it. Otherwise, estimate realistically (e.g., 20 mins within city, 45 mins between cities).
- **Entertainment:** If the search results show NO movies/events, explicitly state in the 'description' "No live cinema events found, opted for [Alternative Activity]".

OUTPUT JSON FORMAT:
{
  "meta": { 
    "summary": "One sentence overview of the plan.", 
    "weather_advice": "General advice (e.g., carry an umbrella)." 
  },
  "timeline": [
    {
      "time_slot": "14:00 - 15:30",
      "activity_type": "FOOD | ACTIVITY | TRAVEL",
      "title": "Name of Place",
      "address": "Area, City",
      "description": "Why this fits the mood + details about food/activity.",
      "coords": { "lat": 28.5, "lng": 77.2 }, 
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
    # If it's a full trip, the destination is usually passed or derived. 
    # Assuming Frontend sends 'destination' in context for 'TRIP' mode.
    search_city = data['context'].get('destination', current_location)
    
    # Fallback if no destination provided for TRIP
    if trip_type == 'TRIP' and search_city == current_location:
        search_city = data['context'].get('user_notes', current_location) # Use notes as destination if needed

    # Coordinates of starting point
    start_coords = None
    coords_data = data['context'].get('coordinates')
    if coords_data:
        start_coords = (coords_data['lat'], coords_data['lng'])

    # Date for movie searches
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # --- 2. AGGRESSIVE DATA FETCHING (SPLIT SEARCH) ---
    search_results_text = ""
    
    if tavily:
        try:
            # A. SPECIFIC MOVIE SEARCH (Fixes the "No Movies" bug)
            movie_query = f"Movies and events showing in {search_city} on {today_str}"
            print(f"Searching Movies: {movie_query}")
            movies = tavily.search(query=movie_query, max_results=3)
            
            # B. GENERAL PLACE SEARCH
            place_query = f"Best tourist spots, restaurants, and things to do in {search_city}"
            print(f"Searching Places: {place_query}")
            places = tavily.search(query=place_query, max_results=8)
            
            # Combine results for context
            combined_results = movies.get('results', []) + places.get('results', [])
            search_results_text = json.dumps(combined_results)
            
        except Exception as e:
            print(f"Search Error: {e}")
            search_results_text = "No external search data available."

    # --- 3. PRE-CALCULATE DISTANCES (If Geopy is available) ---
    # We inject this into the prompt to help the AI estimate better
    distance_hint = ""
    if HAS_GEOPY and start_coords and combined_results:
        # Calculate distance to first found place just to give AI a sense of scale
        # This is a simplified logic for MVP
        pass 

    # --- 4. GEMINI GENERATION ---
    try:
        model_name = get_live_model()
        
        # Inject variables into the prompt
        formatted_prompt = SYSTEM_PROMPT.format(
            plan_type=trip_type,
            search_city=search_city,
            current_date=today_str
        )
        
        full_prompt = f"""
        {formatted_prompt}
        
        USER INPUT DATA: {json.dumps(data)}
        
        EXTERNAL SEARCH RESULTS (Use these for the plan):
        {search_results_text}
        """
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        headers = {'Content-Type': 'application/json'}
        payload = { "contents": [{ "parts": [{"text": full_prompt}] }] }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            print(f"GOOGLE ERROR: {response.text}")
            # Return a fallback so the user sees SOMETHING
            return jsonify({
                "meta": {"summary": "AI currently overloaded. Please try again."},
                "timeline": []
            })

        json_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        
        # Clean markdown if present
        clean_json = json_text.replace("```json", "").replace("```", "").strip()
        
        final_data = json.loads(clean_json)
        
        # --- 5. POST-PROCESSING (Polish Travel Times) ---
        # We can attempt to refine the travel times here if we had real coords for every spot
        # For now, we trust the AI's estimation based on the prompt instructions
        
        return jsonify(final_data)
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

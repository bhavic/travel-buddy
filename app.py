import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient
from datetime import datetime

# GEOPY SUPPORT
try:
    from geopy.distance import geodesic
    HAS_GEOPY = True
except ImportError:
    HAS_GEOPY = False

app = Flask(__name__)
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

tavily = None
if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)

# --- MASTER KEY FUNCTION (Robust Connection) ---
def ask_google_brute_force(prompt):
    endpoints = [
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}",
    ]
    for url in endpoints:
        try:
            response = requests.post(url, headers={'Content-Type': 'application/json'}, json={ "contents": [{ "parts": [{"text": prompt}] }] })
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
        except: pass
    raise Exception("Google AI Unreachable")

# --- STRICT PROMPT FOR REAL DATA ---
SYSTEM_PROMPT = """
You are "TravelBuddy Pro", a decisve local expert.
OBJECTIVE: Create a highly specific, real-time itinerary.

DATA RULES (CRITICAL):
1. **REAL PLACES ONLY:** You must pick specific, real places found in the "SEARCH RESULTS". Never say "Local Cafe".
2. **OPERATING HOURS:** Check the search snippets. If a place closes at 10 PM, do not schedule it for 11 PM.
3. **RATINGS:** Only suggest places with implied high quality (4.0+ stars).
4. **DECISION MAKING:** Do not give options. Pick the BEST one and explain WHY.

OUTPUT FORMAT (JSON):
{{
  "meta": {{ "summary": "Direct, punchy summary of the plan.", "weather_advice": "Brief weather note." }},
  "timeline": [
    {{
      "time_slot": "19:00 - 20:30",
      "activity_type": "DINNER",
      "title": "Comorin",
      "address": "Two Horizon Center, Gurugram",
      "description": "Rated 4.9⭐. Known for their Champaran Meat and Sous Vide Gin. Great modern vibe.",
      "tags": ["⭐ 4.9", "Modern Indian", "$$$"],
      "open_status": "Closes 1 AM",
      "estimated_travel_time_next": "15 mins",
      "google_query": "Comorin Gurugram"
    }}
  ]
}}
"""

@app.route('/api/plan', methods=['POST'])
def plan_trip():
    data = request.json
    print("Received Data:", data)

    # 1. PARSE CONTEXT
    trip_type = data.get('plan_type', 'NOW')
    location = data['context'].get('location', 'Gurugram')
    dest = data['context'].get('destination', location)
    if trip_type == 'TRIP' and dest == location: dest = data['context'].get('user_notes', location)
    
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 2. SMART SEARCH (Specific Queries)
    search_results_text = ""
    if tavily:
        try:
            # We fire multiple specific queries to get "Review" quality data
            queries = [
                f"Best highly-rated restaurants in {dest} open now",
                f"Top tourist attractions in {dest} closing time and reviews",
                f"Hidden gems and cool activities in {dest} for couples/groups"
            ]
            
            combined_results = []
            for q in queries:
                res = tavily.search(query=q, max_results=4, include_domains=[]) # You can restrict domains if needed
                combined_results.extend(res.get('results', []))
            
            search_results_text = json.dumps(combined_results)
        except Exception as e:
            print(f"Search Error: {e}")

    # 3. GENERATE
    try:
        start_coords = data['context'].get('coordinates')
        dist_hint = f"Start: {start_coords}" if start_coords else "Calculate distances inside the city."

        full_prompt = f"""
        {SYSTEM_PROMPT}
        
        CONTEXT:
        - Plan: {trip_type}
        - City: {dest}
        - Current Time: {today_str}
        - User Vibe: {json.dumps(data.get('users'))}
        - Location Context: {dist_hint}
        
        REAL-TIME SEARCH DATA (Review these snippets for Ratings/Hours):
        {search_results_text}
        """
        
        json_text = ask_google_brute_force(full_prompt)
        clean_json = json_text.replace("```json", "").replace("```", "").strip()
        return jsonify(json.loads(clean_json))
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

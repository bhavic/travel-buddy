import os
import json
import requests
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient
from datetime import datetime

app = Flask(__name__)
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

tavily = None
if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)

# --- MASTER KEY CONNECTION FUNCTION (The Fix) ---
def ask_google(prompt):
    # We try ALL possible endpoints. One of these WILL work.
    endpoints = [
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}",
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.0-pro:generateContent?key={GEMINI_API_KEY}"
    ]
    
    last_err = ""
    print("⚡ Starting Connection Sequence...")
    
    for url in endpoints:
        model_name = url.split("models/")[1].split(":")[0]
        print(f"   👉 Trying {model_name}...")
        
        try:
            payload = {
                "contents": [{ "parts": [{"text": prompt}] }],
                "generationConfig": { "temperature": 0.4 } 
            }
            response = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload)
            
            if response.status_code == 200:
                print(f"   ✅ SUCCESS with {model_name}!")
                return response.json()['candidates'][0]['content']['parts'][0]['text']
            else:
                print(f"   ❌ Failed: {response.status_code} - {response.text}")
                last_err = response.text
        except Exception as e:
            print(f"   ❌ Connection Error: {e}")
            last_err = str(e)
            
    # If we get here, everything failed.
    raise Exception(f"All Google Models Failed. Last Error: {last_err}")

# --- STRICT PROMPT ---
SYSTEM_PROMPT = """
You are "TripBuddy", a strict local guide.
OBJECTIVE: Plan a specific itinerary.

RULES:
1. **SPECIFICITY IS MANDATORY:** You MUST name real, verifiable places.
   - ❌ BAD: "Local Dhabha", "Nearby Cafe", "City Park"
   - ✅ GOOD: "Gulshan Dhaba", "Roots Cafe", "Leisure Valley Park"
2. **USE SEARCH DATA:** Use the provided search results to pick the best rated spots.
3. **REALISTIC TIMING:** Account for travel time.
4. **JSON ONLY:** Output pure JSON. No markdown.

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
    return "Travel Buddy V3.1 is Running!", 200

@app.route('/api/plan', methods=['POST'])
def plan_trip():
    try:
        data = request.json
        print("Received Data:", data)

        # 1. SETUP
        trip_type = data.get('plan_type', 'NOW')
        location = data['context'].get('location', 'Gurugram')
        dest = data['context'].get('destination', location)
        if trip_type == 'TRIP' and (not dest or dest == location): 
            dest = data['context'].get('user_notes', location)

        # 2. SEARCH (Get Specifics)
        search_context = "No search data."
        if tavily:
            try:
                # Specific query to force real names
                q = f"Best rated restaurants and specific tourist attractions in {dest} with names and ratings"
                print(f"🔎 Searching Tavily: {q}")
                res = tavily.search(query=q, max_results=6)
                search_context = json.dumps(res.get('results', []))
            except Exception as e:
                print(f"Search Error: {e}")

        # 3. GENERATE
        full_prompt = f"""
        {SYSTEM_PROMPT}
        
        CONTEXT:
        - Plan: {trip_type}
        - City: {dest}
        - Vibe: {json.dumps(data.get('users'))}
        
        AVAILABLE PLACES (Pick from here):
        {search_context}
        """
        
        raw_response = ask_google(full_prompt)
        
        # 4. CLEAN JSON
        clean_json = raw_response.replace("```json", "").replace("```", "").strip()
        
        # 5. VALIDATE (The "No Generic" Check)
        if "Local Eatery" in clean_json or "Local Restaurant" in clean_json:
            print("⚠️ Detected generic response. Retrying...")
            full_prompt += "\n\nCRITICAL ERROR: You provided generic names. REWRITE with specific real names."
            raw_response = ask_google(full_prompt)
            clean_json = raw_response.replace("```json", "").replace("```", "").strip()

        return jsonify(json.loads(clean_json))

    except Exception as e:
        print(f"Server Error: {e}")
        # Send the exact error to the frontend so we can see it
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

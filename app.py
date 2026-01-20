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

# --- 1. DYNAMIC MODEL DISCOVERY (Fixes 404 Errors) ---
def get_working_model_url():
    """Asks Google which model is valid for this API Key."""
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        response = requests.get(list_url)
        data = response.json()
        valid_models = []
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    valid_models.append(m['name'])
        
        # Fallback if list is empty
        if not valid_models:
            return f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
            
        # Select best available model (Prefer Flash or Pro)
        selected = valid_models[0]
        for m in valid_models:
            if "flash" in m or "1.5" in m:
                selected = m
                break
                
        clean_name = selected.replace("models/", "")
        return f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={GEMINI_API_KEY}"
    except:
        return f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"

def ask_google(prompt):
    url = get_working_model_url()
    # We use temperature 0.5 to allow some creativity if search fails
    payload = { "contents": [{ "parts": [{"text": prompt}] }], "generationConfig": { "temperature": 0.5 } }
    
    response = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload)
    if response.status_code == 200:
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    else:
        raise Exception(f"Google Error {response.status_code}: {response.text}")

# --- 2. INTELLIGENT PROMPT (Fixes "Unable to Plan" Error) ---
SYSTEM_PROMPT = """
You are "TripBuddy", a local expert guide.
OBJECTIVE: Plan a specific itinerary.

RULES:
1. **SPECIFICITY:** You must name real, verifiable places.
2. **SEARCH DATA FIRST:** Prioritize the 'AVAILABLE PLACES' provided below.
3. **FALLBACK MANDATORY:** If 'AVAILABLE PLACES' is empty, **YOU MUST USE YOUR OWN INTERNAL KNOWLEDGE** to suggest top-rated, real places. Do NOT return an error.
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
    return "Travel Buddy Final Version is Running!", 200

@app.route('/api/plan', methods=['POST'])
def plan_trip():
    try:
        data = request.json
        print("Received Data:", data)

        # --- 3. SAFETY NET (Fixes Missing City Error) ---
        trip_type = data.get('plan_type', 'NOW')
        loc_input = data['context'].get('location', '')
        dest_input = data['context'].get('destination', '')
        
        # Default to Gurugram if user sends nothing
        target_city = "Gurugram" 
        
        if trip_type == 'TRIP':
            if dest_input and dest_input.strip(): target_city = dest_input
            elif loc_input and loc_input.strip(): target_city = loc_input
        else:
            if loc_input and loc_input.strip(): target_city = loc_input
            
        print(f"🎯 Target City: {target_city}")

        # --- 4. SEARCH & FALLBACK ---
        search_context = ""
        if tavily:
            try:
                q = f"Top rated tourist attractions and restaurants in {target_city} for {data.get('users', {}).get('vibe', 'general')} vibe"
                print(f"🔎 Searching Tavily: {q}")
                res = tavily.search(query=q, max_results=6)
                if res.get('results'):
                    search_context = json.dumps(res['results'])
                    print(f"✅ Found {len(res['results'])} results.")
                else:
                    print("⚠️ Tavily returned 0 results. Switching to Internal Knowledge.")
            except Exception as e:
                print(f"❌ Search Error: {e}")

        # --- 5. GENERATE ---
        full_prompt = f"""
        {SYSTEM_PROMPT}
        
        CONTEXT:
        - Plan Type: {trip_type}
        - Target City: {target_city}
        - User Vibe: {json.dumps(data.get('users'))}
        
        AVAILABLE PLACES FROM SEARCH:
        {search_context if search_context else "Search failed. Use your internal knowledge for " + target_city}
        """
        
        raw_response = ask_google(full_prompt)
        clean_json = raw_response.replace("```json", "").replace("```", "").strip()
        
        # --- 6. VALIDATION LOOP (Fixes "Local Eatery" Lazy Answers) ---
        if "Local Eatery" in clean_json or "Local Restaurant" in clean_json:
            print("⚠️ Generic answer detected. Forcing retry...")
            full_prompt += "\n\nERROR: You used generic names. REWRITE using SPECIFIC REAL PLACE NAMES."
            raw_response = ask_google(full_prompt)
            clean_json = raw_response.replace("```json", "").replace("```", "").strip()

        return jsonify(json.loads(clean_json))

    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

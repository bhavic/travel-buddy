import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient

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

# 2. UPDATED SYSTEM PROMPT (The "Guide" Logic)
SYSTEM_PROMPT = """
You are the "Dynamic Trip Companion".
OBJECTIVE: Return a JSON plan based on user inputs.

RULES:
1. Prioritize "User Places" (Bucket List) if provided.
2. If "User Places" is empty, use "Search Results".
3. If Search Results are empty, USE YOUR KNOWLEDGE.
4. **CRITICAL NEW RULE:** You must provide a "mini_guide" and "travel_time" for every location.
5. **CRITICAL NEW RULE:** Explicitly state if there are no live shows or movies nearby in the "entertainment_note".

OUTPUT JSON FORMAT:
{
  "meta": { "summary": "1 sentence reasoning." },
  "view_type": "DECK", 
  "decks": [
    { 
      "title": "Top Picks", 
      "cards": [ 
        { 
          "name": "Place Name", 
          "tagline": "Why it fits", 
          "match_score": 95, 
          "travel_time": "approx 45 mins from [User Location]",
          "things_to_do": ["Activity 1", "Activity 2", "Activity 3"],
          "entertainment_note": "No live shows or movies nearby.",
          "status": "Open" 
        } 
      ] 
    } 
  ],
  "timeline": []
}
"""

@app.route('/', methods=['GET'])
def health_check():
    return "Dynamic Trip Companion is Alive!", 200

@app.route('/api/plan', methods=['POST'])
def plan_trip():
    data = request.json
    print("Received Data:", data)

    location = data['context'].get('location', 'Gurugram')
    coords = data['context'].get('coordinates')
    search_loc = f"{coords['lat']},{coords['lng']}" if coords else location
    
    search_context = "No external search results found."
    
    # Tavily Search (Broader search for events too)
    try:
        query = ""
        if data.get('user_places'):
            query = f"Details for {data['user_places']} in {location}"
        else:
            # We add 'events' to the search to see if any exist
            query = f"Best places, hidden gems, and live events near {search_loc} for {data['users'][0]['energy']} vibe"
            
        print(f"Searching: {query}")
        
        if tavily:
            tavily_response = tavily.search(query=query, max_results=4)
            if 'results' in tavily_response and len(tavily_response['results']) > 0:
                search_context = json.dumps(tavily_response['results'])
            
    except Exception as e:
        print(f"Search Error: {e}")

    # Gemini Generation
    try:
        model_name = get_live_model()
        full_prompt = f"{SYSTEM_PROMPT}\n\nUSER LOCATION: {location}\nUSER DATA: {json.dumps(data)}\nSEARCH RESULTS: {search_context}"
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        headers = {'Content-Type': 'application/json'}
        payload = { "contents": [{ "parts": [{"text": full_prompt}] }] }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            print(f"GOOGLE ERROR: {response.text}")
            raise Exception(f"Google Error: {response.text}")

        json_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        clean_json = json_text.replace("```json", "").replace("```", "")
        
        return jsonify(json.loads(clean_json))
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

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

# ---------------------------------------------------------
# 1. AUTO-DISCOVERY FUNCTION (Solves the 404 Error)
# ---------------------------------------------------------
def get_live_model():
    """
    Asks Google which model is available for this Key.
    """
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
        response = requests.get(url)
        data = response.json()
        
        # Priority list of models we prefer
        preferred = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.0-pro", "gemini-pro"]
        
        available_models = []
        if 'models' in data:
            for m in data['models']:
                # We only want models that can generate content
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    # Clean name "models/gemini-pro" -> "gemini-pro"
                    clean_name = m['name'].replace("models/", "")
                    available_models.append(clean_name)
        
        # Pick the best one
        for p in preferred:
            if p in available_models:
                print(f"⚡ LOCKED ON MODEL: {p}")
                return p
        
        # If none of our favorites are there, take the first available one
        if available_models:
            print(f"⚡ FALLBACK MODEL: {available_models[0]}")
            return available_models[0]
            
    except Exception as e:
        print(f"Model Discovery Failed: {e}")
        
    return "gemini-pro" # Last resort

# ---------------------------------------------------------
# 2. CREATIVE PROMPT (Solves the "Cannot generate plan" Error)
# ---------------------------------------------------------
SYSTEM_PROMPT = """
You are the "Dynamic Trip Companion".
OBJECTIVE: Return a JSON plan based on user inputs.

RULES:
1. Prioritize "User Places" (Bucket List) if provided.
2. If "User Places" is empty, use "Search Results".
3. **CRITICAL:** If Search Results are also empty, USE YOUR OWN KNOWLEDGE of the city to create a plan. **NEVER** return an error message. **ALWAYS** return a valid JSON plan.

OUTPUT JSON FORMAT:
{
  "meta": { "summary": "1 sentence reasoning." },
  "view_type": "DECK", 
  "tie_breaker_game": null,
  "decks": [
    { 
      "title": "Top Picks", 
      "cards": [ 
        { "name": "Place Name", "tagline": "Why it fits", "match_score": 95, "status": "Open" } 
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

    # Setup Location
    location = data['context'].get('location', 'Gurugram')
    coords = data['context'].get('coordinates')
    search_loc = f"{coords['lat']},{coords['lng']}" if coords else location
    
    search_context = "No external search results found."
    
    # Tavily Search
    try:
        query = ""
        if data.get('user_places'):
            query = f"Details for {data['user_places']} in {location}"
        else:
            query = f"Best places open now near {search_loc} for {data['users'][0]['energy']} vibe"
            
        print(f"Searching: {query}")
        
        if tavily:
            tavily_response = tavily.search(query=query, max_results=3)
            if 'results' in tavily_response and len(tavily_response['results']) > 0:
                search_context = json.dumps(tavily_response['results'])
            
    except Exception as e:
        print(f"Search Error: {e}")

    # Gemini Generation
    try:
        # 1. Find the model
        model_name = get_live_model()
        
        # 2. Build the request
        full_prompt = f"{SYSTEM_PROMPT}\n\nUSER DATA: {json.dumps(data)}\nSEARCH RESULTS: {search_context}"
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        headers = {'Content-Type': 'application/json'}
        payload = { "contents": [{ "parts": [{"text": full_prompt}] }] }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            print(f"GOOGLE ERROR: {response.text}")
            raise Exception(f"Google Error: {response.text}")

        # 3. Clean and Return
        json_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        clean_json = json_text.replace("```json", "").replace("```", "")
        
        return jsonify(json.loads(clean_json))
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

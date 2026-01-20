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

def ask_google_ai(prompt):
    # CHANGED TO V1 (Stable) and gemini-pro
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code != 200:
        # Print exact error to logs
        print(f"GOOGLE ERROR: {response.text}")
        raise Exception(f"Google Error {response.status_code}: {response.text}")
        
    return response.json()['candidates'][0]['content']['parts'][0]['text']

SYSTEM_PROMPT = """
You are the "Dynamic Trip Companion".
OBJECTIVE: Return a JSON plan.
- Prioritize Bucket List.
- If empty, use Search Results.

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

    # 1. SETUP LOCATION
    location = data['context'].get('location', 'Gurugram')
    coords = data['context'].get('coordinates')
    search_loc = f"{coords['lat']},{coords['lng']}" if coords else location
    
    search_context = "No search performed."
    
    # 2. TAVILY SEARCH
    try:
        query = ""
        if data.get('user_places'):
            query = f"Details for {data['user_places']} in {location}"
        else:
            query = f"Best places open now near {search_loc} for {data['users'][0]['energy']} vibe"
            
        print(f"Searching: {query}")
        
        if tavily:
            tavily_response = tavily.search(query=query, max_results=3)
            search_context = json.dumps(tavily_response['results'])
            
    except Exception as e:
        print(f"Search Error: {e}")

    # 3. GEMINI GENERATION
    try:
        full_prompt = f"{SYSTEM_PROMPT}\n\nUSER DATA: {json.dumps(data)}\nSEARCH RESULTS: {search_context}"
        
        json_response_string = ask_google_ai(full_prompt)
        
        # Clean markdown
        clean_json = json_response_string.replace("```json", "").replace("```", "")
        
        return jsonify(json.loads(clean_json))
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

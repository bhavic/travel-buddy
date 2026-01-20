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
    # ------------------------------------------------------------------
    # FIX: We use the 'v1' endpoint which is the most stable for all keys.
    # We use 'gemini-pro' which is the standard text model.
    # ------------------------------------------------------------------
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code != 200:
        print(f"GOOGLE ERROR: {response.text}")
        # Fallback: If v1 fails, try v1beta with 1.5-flash as a backup
        return ask_google_ai_backup(prompt)
        
    return response.json()['candidates'][0]['content']['parts'][0]['text']

def ask_google_ai_backup(prompt):
    print("⚠️ Trying Backup Model (Flash)...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = { "contents": [{ "parts": [{"text": prompt}] }] }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise Exception(f"All Models Failed. Google Error: {response.text}")
    return response.json()['candidates'][0]['content']['parts'][0]['text']

# ------------------------------------------------------------------
# PROMPT UPDATE: We force the AI to generate a plan even with no data.
# ------------------------------------------------------------------
SYSTEM_PROMPT = """
You are the "Dynamic Trip Companion".
OBJECTIVE: Return a JSON plan based on user inputs.

RULES:
1. Prioritize "User Places" (Bucket List) if provided.
2. If "User Places" is empty, use "Search Results".
3. **CRITICAL:** If Search Results are also empty, USE YOUR OWN KNOWLEDGE of the city to create a plan. DO NOT return an error message. Always return a valid JSON plan.

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

    location = data['context'].get('location', 'Gurugram')
    coords = data['context'].get('coordinates')
    search_loc = f"{coords['lat']},{coords['lng']}" if coords else location
    
    search_context = "No external search results found."
    
    # TAVILY SEARCH
    try:
        query = ""
        if data.get('user_places'):
            query = f"Details for {data['user_places']} in {location}"
        else:
            query = f"Best places open now near {search_loc} for {data['users'][0]['energy']} vibe"
            
        print(f"Searching: {query}")
        
        if tavily:
            tavily_response = tavily.search(query=query, max_results=3)
            # Check if results exist
            if 'results' in tavily_response and len(tavily_response['results']) > 0:
                search_context = json.dumps(tavily_response['results'])
            else:
                print("Tavily returned 0 results.")
            
    except Exception as e:
        print(f"Search Error: {e}")

    # GEMINI GENERATION
    try:
        full_prompt = f"{SYSTEM_PROMPT}\n\nUSER DATA: {json.dumps(data)}\nSEARCH RESULTS: {search_context}"
        
        json_response_string = ask_google_ai(full_prompt)
        
        # Clean markdown if present
        clean_json = json_response_string.replace("```json", "").replace("```", "")
        
        return jsonify(json.loads(clean_json))
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

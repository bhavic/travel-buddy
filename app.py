import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from tavily import TavilyClient

app = Flask(__name__)
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)

SYSTEM_PROMPT = """
You are the "Dynamic Trip Companion" for Bhavic and Bhaavya.
INPUTS: User profiles, Location, Mode (Solo/Duo), Plan Type, and "Bucket List".

OBJECTIVE: Return a JSON plan.
- If "Bucket List" exists, prioritize those places.
- If "Bucket List" is empty, use Search Results to find spots matching the Vibe.
- If 12AM-5AM, suggest only safe/24hr places.

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

    # 1. SETUP SEARCH LOCATION
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
        
        if TAVILY_API_KEY:
            tavily_response = tavily.search(query=query, max_results=3)
            search_context = json.dumps(tavily_response['results'])
            
    except Exception as e:
        print(f"Search Error: {e}")

    # 3. GEMINI GENERATION
    try:
        # We use the specific 1.5-flash model which is the current stable free one
        model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=SYSTEM_PROMPT)
        
        user_prompt = f"USER DATA: {json.dumps(data)}\nSEARCH RESULTS: {search_context}"
        
        response = model.generate_content(user_prompt, generation_config={"response_mime_type": "application/json"})
        return jsonify(json.loads(response.text))
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

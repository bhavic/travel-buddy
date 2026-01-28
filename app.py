"""
Travel Buddy - Gemini-Powered Travel Assistant
Simple backend that lets Gemini 2.0 Flash handle all planning with Google Search grounding.
"""

import os
import json
import re
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "allow_headers": ["Content-Type", "Accept"],
    "methods": ["GET", "POST", "OPTIONS"]
}})

# Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Gemini API endpoint (with Google Search grounding)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# System prompt for Gemini
SYSTEM_PROMPT = """You are Travel Buddy, a helpful local travel assistant that creates personalized plans.

CRITICAL RULES - FOLLOW THESE EXACTLY:

1. TIME AWARENESS (VERY IMPORTANT):
   - The user's CURRENT time is provided in the context. All plans MUST start AFTER this time.
   - If it's 11 PM, don't suggest activities starting at 9 PM (that's in the past!)
   - If user says "anytime today" and it's late, suggest TOMORROW instead
   - Always acknowledge the current time in your plan

2. LOCATION AWARENESS (VERY IMPORTANT):
   - Use the user's coordinates to find the NEAREST theaters, restaurants, etc.
   - Search for "theaters near [user's location]" not generic city-wide searches
   - Never suggest places 30+ mins away when closer options exist
   - Include actual distance/travel time from user's location

3. REAL DATA ONLY:
   - Search Google for ACTUAL current movie showtimes
   - Include real showtime like "7:30 PM show at PVR" not "check BookMyShow"
   - If you can't find showtimes, say so honestly

4. PARKING (when user has car):
   - Include specific parking info: "Basement parking available, ₹50/hour"
   - Mention if parking is free with movie ticket

5. FOOD DETAILS:
   - Real restaurant names with cuisine type
   - Average cost per person
   - Whether reservation needed

You MUST respond in this exact JSON format (no markdown, just pure JSON):
{
  "greeting": "A friendly greeting that acknowledges current time and location",
  "type": "day_plan",
  "day_title": "Title of the plan",
  "timeline": [
    {
      "time": "7:30 PM",
      "emoji": "🚗",
      "activity": "What to do",
      "place": "Specific place name",
      "details": "Address, parking info, costs, practical details",
      "google_query": "Exact place name for Google Maps",
      "travel_time_to_next": "10 mins by car"
    }
  ],
  "total_budget_estimate": "₹X,XXX - ₹X,XXX",
  "tips": ["Practical tip 1", "Practical tip 2"],
  "closing": "Sign-off"
}

Remember: 
- Times must be AFTER the user's current time
- Places must be NEAR the user's location  
- Include REAL showtimes, prices, parking info"""


def call_gemini(user_query: str, context: dict, preferences: dict) -> dict:
    """Call Gemini 2.0 Flash with Google Search grounding."""
    
    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not configured"}
    
    # Ensure context and preferences are not None
    context = context or {}
    preferences = preferences or {}
    
    # Build context string with null safety
    location = context.get('location') or 'Unknown'
    coords = context.get('coordinates') or {}
    local_time = context.get('local_time') or datetime.now().strftime('%H:%M')
    local_hour = context.get('local_hour') or datetime.now().hour
    timezone = context.get('timezone') or 'Asia/Kolkata'
    
    # Determine time of day
    if local_hour < 6:
        time_of_day = "late night"
    elif local_hour < 12:
        time_of_day = "morning"
    elif local_hour < 17:
        time_of_day = "afternoon"
    elif local_hour < 21:
        time_of_day = "evening"
    else:
        time_of_day = "night"
    
    # Build user context
    context_str = f"""
USER CONTEXT:
- Location: {location}
- Coordinates: {coords.get('lat', 'N/A')}, {coords.get('lng', 'N/A')}
- Current Time: {local_time} ({time_of_day})
- Timezone: {timezone}
- Date: {datetime.now().strftime('%A, %B %d, %Y')}

USER PREFERENCES:
- Food: {preferences.get('food', 'any')}
- Budget: {preferences.get('budget', 'standard')}
- Vibe: {preferences.get('vibe', 'balanced')}

USER REQUEST:
{user_query}

Search for real, current information and create a helpful plan. Use actual place names, addresses, and current showtimes/hours."""

    # Prepare API request with Google Search grounding
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": context_str}]
            }
        ],
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "tools": [
            {
                "google_search": {}
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 4096
        }
    }
    
    try:
        response = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=60
        )
        
        if response.status_code != 200:
            print(f"❌ Gemini API Error: {response.status_code}")
            print(response.text)
            return create_fallback_response(user_query, location)
        
        result = response.json()
        
        # Extract the text response
        candidates = result.get('candidates', [])
        if not candidates:
            print("❌ No candidates in response")
            return create_fallback_response(user_query, location)
        
        content = candidates[0].get('content', {})
        parts = content.get('parts', [])
        if not parts:
            print("❌ No parts in response")
            return create_fallback_response(user_query, location)
        
        text = parts[0].get('text', '')
        print(f"📝 Gemini response length: {len(text)} chars")
        
        # Parse JSON response
        try:
            # Clean up the response (remove markdown if present)
            text = text.strip()
            
            # Try to find JSON object in the text
            # First, try direct parse
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
            
            # Remove markdown code blocks if present
            if '```' in text:
                # Extract content between ```json and ```
                json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
                if json_match:
                    text = json_match.group(1).strip()
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        pass
            
            # Try to find JSON object by looking for { and }
            start_idx = text.find('{')
            end_idx = text.rfind('}')
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = text[start_idx:end_idx + 1]
                return json.loads(json_str)
            
            # If all parsing fails
            print(f"❌ Could not parse JSON from response")
            print(f"Raw text preview: {text[:300]}")
            return create_fallback_response(user_query, location)
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON Parse Error: {e}")
            print(f"Raw text: {text[:500]}")
            return create_fallback_response(user_query, location)
            
    except requests.exceptions.Timeout:
        print("❌ Gemini API Timeout")
        return create_fallback_response(user_query, location)
    except Exception as e:
        print(f"❌ Gemini API Exception: {e}")
        import traceback
        traceback.print_exc()
        return create_fallback_response(user_query, location)


def create_fallback_response(query: str, location: str) -> dict:
    """Create a fallback response when Gemini fails."""
    return {
        "greeting": f"I'm having trouble searching right now, but here are some ideas for {location}! 🌟",
        "type": "itinerary",
        "cards": [
            {
                "emoji": "🔍",
                "title": "Search on Google Maps",
                "subtitle": "Find great places near you",
                "card_type": "primary",
                "options": [
                    {
                        "name": f"Search: {query}",
                        "highlight": "Tap to search on Google Maps",
                        "details": f"Find options in {location}",
                        "google_query": f"{query} in {location}",
                        "tags": ["Search"]
                    }
                ]
            }
        ],
        "closing": "Try again in a moment, or search directly on Google Maps!"
    }


# ===========================================
# API ENDPOINTS
# ===========================================

@app.route('/api/assist', methods=['POST', 'OPTIONS'])
def assist():
    """Main endpoint - sends everything to Gemini for intelligent planning."""
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        data = request.json or {}
        
        query = data.get('query', '')
        context = data.get('context', {})
        preferences = data.get('preferences', {})
        
        print(f"📥 Query: {query}")
        print(f"📍 Location: {context.get('location', 'Unknown')}")
        print(f"🕐 Time: {context.get('local_time', 'Unknown')}")
        
        if not query:
            return jsonify({
                "greeting": "Hey! What would you like to do? 🤔",
                "type": "error",
                "cards": [],
                "closing": "Tell me what you're in the mood for!"
            })
        
        # Call Gemini with full context
        result = call_gemini(query, context, preferences)
        
        return jsonify(result)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({
            "greeting": "Oops, something went wrong! 😅",
            "type": "error",
            "cards": [],
            "closing": "Please try again!"
        }), 500


@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "gemini_configured": bool(GEMINI_API_KEY),
        "timestamp": datetime.now().isoformat()
    })


@app.route('/', methods=['GET'])
def home():
    """Home page."""
    return jsonify({
        "name": "Travel Buddy API",
        "version": "2.0 (Gemini-powered)",
        "endpoints": {
            "/api/assist": "POST - Main assistant endpoint",
            "/api/health": "GET - Health check"
        }
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Travel Buddy starting on port {port}")
    print(f"🔑 Gemini API Key: {'✅ Configured' if GEMINI_API_KEY else '❌ Missing'}")
    app.run(host='0.0.0.0', port=port, debug=True)

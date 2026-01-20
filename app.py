"""
Travel Buddy Pro - Backend with Emotional Intelligence
A human-centered travel planning API that understands traveler psychology.
"""

import os
import json
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from tavily import TavilyClient

# --- GEOPY SETUP ---
try:
    from geopy.geocoders import Nominatim
    geolocator = Nominatim(user_agent="travel_buddy_pro_v2")
    HAS_GEOPY = True
    print("✅ Geopy Active - Location intelligence enabled")
except ImportError:
    HAS_GEOPY = False
    print("⚠️ Geopy not installed - Using text locations only")

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")

tavily = None
if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)
    print("✅ Tavily Active - Real-time search enabled")

# --- DYNAMIC MODEL DISCOVERY ---
def get_working_model_url():
    """Asks Google for available models and selects the best one."""
    print("🔎 Discovering available AI models...")
    list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    
    try:
        response = requests.get(list_url, timeout=10)
        data = response.json()
        
        valid_models = []
        if 'models' in data:
            for m in data['models']:
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    valid_models.append(m['name'])
        
        if not valid_models:
            print("⚠️ No models found. Using fallback.")
            return f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
        
        # Prefer flash models for speed
        selected = valid_models[0]
        for m in valid_models:
            if "flash" in m.lower() or "1.5" in m:
                selected = m
                break
        
        clean_name = selected.replace("models/", "")
        print(f"✅ Selected Model: {clean_name}")
        return f"https://generativelanguage.googleapis.com/v1beta/models/{clean_name}:generateContent?key={GEMINI_API_KEY}"
    
    except Exception as e:
        print(f"⚠️ Discovery failed: {e}")
        return f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"


# --- ENHANCED SYSTEM PROMPT ---
SYSTEM_PROMPT = """
You are "TripBuddy" — a warm, knowledgeable travel companion who genuinely cares about creating meaningful experiences.

## YOUR PERSONALITY
- You speak like a well-traveled friend, not a booking engine
- You understand that travel is emotional — people seek escape, connection, celebration, or discovery
- You notice small details that make experiences special (best table for sunset views, the barista who remembers your name)
- You balance practicality with wonder

## TRAVELER CONTEXT YOU'LL RECEIVE
- **Group**: solo / couple / friends / family / group (adjust intimacy and logistics)
- **Occasion**: celebration / escape / exploration / romance / worktrip (set the emotional tone)
- **Pace**: packed / balanced / slow (determines activity density)
- **Food Style**: street / casual / fine (influences restaurant choices)
- **Crowd Preference**: hidden / mixed / popular (off-beaten vs tourist spots)
- **Constraints**: mobility / dietary / budget / time (hard requirements to respect)
- **Personal Note**: Any special context the traveler shared

## YOUR TASK
Create a **narrative itinerary** that feels like a story, not a spreadsheet.

## WRITING STYLE
- Start each stop with emotional context: "As the morning light filters through..." or "When your energy needs a recharge..."
- Explain WHY a place fits their vibe, not just WHAT it is
- Include sensory details: sounds, smells, textures
- Transition between stops naturally: "A 10-minute walk through the old quarter brings you to..."
- For couples: romantic angles. For families: kid-friendly logistics. For solo: introspection moments.

## RULES
1. **REAL PLACES ONLY**: Name specific, verifiable establishments. Never say "Local Cafe" or "Nearby Restaurant"
2. **RESPECT CONSTRAINTS**: If they said "no long walks," keep distances short. If vegetarian, only suggest veg-friendly spots.
3. **MEAL TIMING**: Lunch around 12:30-14:00, Dinner around 19:00-21:00. Don't schedule activities during natural meal times without food.
4. **PACING**:
   - Packed: 6-8 activities, minimal downtime
   - Balanced: 4-5 activities with breathing room
   - Slow: 2-3 main experiences, lots of lingering time
5. **WEATHER/TIME AWARE**: Morning = outdoor activities before heat. Evening = sunset spots, nightlife.
6. **FALLBACK**: If search results are empty, use your internal knowledge of the city. Never say "I don't have information."

## OUTPUT FORMAT (Strict JSON)
{
  "meta": {
    "greeting": "A warm, personalized opening acknowledging their trip context",
    "summary": "2-3 sentence narrative overview of the day's arc",
    "weather_tip": "Contextual advice if relevant",
    "emotional_note": "A thoughtful observation about their journey"
  },
  "timeline": [
    {
      "time_slot": "09:00 - 10:30",
      "phase": "morning_energy",
      "title": "Specific Place Name",
      "subtitle": "Short atmospheric description",
      "neighborhood": "Area/District Name",
      "narrative": "2-3 sentences telling the story of this stop. Why it matters. What they'll feel.",
      "insider_tip": "One specific tip only a local would know",
      "tags": ["Cafe", "Instagrammable", "Quiet"],
      "vibe_match": "Why this matches their stated preferences",
      "price_indicator": "$$",
      "open_status": "Open until 10 PM",
      "duration": "1.5 hours",
      "transition": "How to get to the next spot (walking, uber, etc) and what they'll see on the way",
      "google_query": "Specific Place Name City",
      "backup_option": "Alternative if this place is full/closed"
    }
  ],
  "closing": {
    "reflection": "End the day's narrative arc with a warm thought",
    "next_day_teaser": "If multi-day, hint at what's coming"
  }
}
"""

# --- HELPER FUNCTIONS ---
def get_time_context(user_hour=None):
    """Returns time context based on user's local time."""
    # Use user's local hour if provided, otherwise use server time
    if user_hour is not None:
        hour = int(user_hour)
    else:
        hour = datetime.now().hour
    
    if hour >= 0 and hour < 5:
        return "late_night", "It's the quiet hours. Most places are closed, but a few gems stay open for night owls. Consider planning for tomorrow morning instead, or I can find late-night spots."
    elif hour >= 5 and hour < 8:
        return "early_morning", "The city is just waking up. Perfect for peaceful starts and early cafes..."
    elif hour >= 8 and hour < 11:
        return "morning", "Morning energy is perfect for exploration before the crowds arrive..."
    elif hour >= 11 and hour < 14:
        return "lunch_time", "Hunger calls, and the city has amazing lunch options..."
    elif hour >= 14 and hour < 17:
        return "afternoon", "The afternoon invites exploration and discovery..."
    elif hour >= 17 and hour < 20:
        return "evening", "Golden hour magic awaits as the city transitions to night..."
    elif hour >= 20 and hour < 23:
        return "night", "The city transforms under the night sky..."
    else:
        return "late_night", "It's getting late. Most spots are winding down, but some night gems await..."


def build_search_query(city, traveler_profile):
    """Builds a rich search query based on traveler profile."""
    group = traveler_profile.get('group', 'travelers')
    occasion = traveler_profile.get('occasion', 'exploration')
    vibe = traveler_profile.get('energy', 'balanced')
    food = traveler_profile.get('food_style', 'casual')
    crowd = traveler_profile.get('crowd_pref', 'mixed')
    
    # Build contextual query
    query_parts = [f"best places in {city}"]
    
    if group == 'couple':
        query_parts.append("romantic")
    elif group == 'family':
        query_parts.append("family friendly kids")
    elif group == 'friends':
        query_parts.append("fun groups")
    elif group == 'solo':
        query_parts.append("solo traveler")
    
    if occasion == 'celebration':
        query_parts.append("special occasion celebration")
    elif occasion == 'escape':
        query_parts.append("peaceful relaxing")
    elif occasion == 'romance':
        query_parts.append("romantic date intimate")
    
    if crowd == 'hidden':
        query_parts.append("hidden gems off beaten path local favorites")
    elif crowd == 'popular':
        query_parts.append("must visit top rated popular")
    
    if food == 'fine':
        query_parts.append("fine dining upscale restaurants")
    elif food == 'street':
        query_parts.append("street food local eateries")
    
    return " ".join(query_parts)


def resolve_location(data):
    """Resolves location from coordinates or text input."""
    trip_type = data.get('plan_type', 'NOW')
    loc_input = data.get('context', {}).get('location', '')
    dest_input = data.get('context', {}).get('destination', '')
    coords = data.get('context', {}).get('coordinates')
    
    # For full trips, use destination
    if trip_type == 'TRIP' and dest_input and len(dest_input) > 2:
        if 'found' not in dest_input.lower() and 'location' not in dest_input.lower():
            return dest_input.strip()
    
    # Try geocoding coordinates
    if coords and HAS_GEOPY:
        try:
            print(f"📍 Geocoding: {coords['lat']}, {coords['lng']}")
            location = geolocator.reverse(f"{coords['lat']}, {coords['lng']}", language='en', timeout=10)
            if location:
                address = location.raw.get('address', {})
                city = address.get('city') or address.get('town') or address.get('suburb') or address.get('state')
                if city:
                    print(f"✅ Resolved to: {city}")
                    return city
        except Exception as e:
            print(f"⚠️ Geocoding failed: {e}")
    
    # Use text input if valid
    if loc_input and 'found' not in loc_input.lower() and 'location' not in loc_input.lower():
        return loc_input.strip()
    
    # Ultimate fallback
    return "Gurugram"


def ask_google(prompt, temperature=0.7):
    """Sends prompt to Google AI with retry logic."""
    url = get_working_model_url()
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 4096
        }
    }
    
    try:
        response = requests.post(
            url, 
            headers={'Content-Type': 'application/json'}, 
            json=payload, 
            timeout=90
        )
        
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            print(f"❌ Google API Error: {response.status_code} - {response.text}")
            raise Exception(f"AI service error: {response.status_code}")
    
    except requests.exceptions.Timeout:
        raise Exception("AI took too long to respond. Please try again.")
    except Exception as e:
        raise Exception(f"AI connection failed: {str(e)}")


def validate_itinerary(response_json, city, time_phase="day"):
    """Validates the AI response and checks for lazy outputs."""
    issues = []
    
    # Check for wrong city
    if "san francisco" in json.dumps(response_json).lower() and city.lower() != "san francisco":
        issues.append("wrong_city")
    
    # Check for generic names
    generic_names = ["local cafe", "nearby restaurant", "city restaurant", "the cafe", "main street cafe"]
    for item in response_json.get('timeline', []):
        title = item.get('title', '').lower()
        if any(generic in title for generic in generic_names):
            issues.append(f"generic_name:{item.get('title')}")
    
    # Check for minimum content - BUT allow fewer stops for late night
    timeline_length = len(response_json.get('timeline', []))
    if time_phase == "late_night":
        # Late night: even 0-1 stops is OK (AI might say "most places are closed")
        if timeline_length < 0:  # Always passes for late night
            issues.append("too_few_stops")
    else:
        # Normal hours: require at least 2 stops
        if timeline_length < 2:
            issues.append("too_few_stops")
    
    return issues


# --- ROUTES ---
@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "Travel Buddy Pro v2.0",
        "features": ["emotional_intelligence", "narrative_itineraries", "real_time_search"]
    }), 200


@app.route('/api/plan', methods=['POST'])
def plan_trip():
    try:
        data = request.json
        print(f"\n{'='*50}")
        print("📥 New Trip Request")
        print(f"{'='*50}")
        print(json.dumps(data, indent=2))
        
        # --- 1. RESOLVE LOCATION ---
        target_city = resolve_location(data)
        print(f"🎯 Target City: {target_city}")
        
        # --- 2. BUILD TRAVELER PROFILE ---
        traveler = data.get('traveler', {})
        profile = {
            "group": traveler.get('group', 'solo'),
            "occasion": traveler.get('occasion', 'exploration'),
            "energy": traveler.get('pace', 'balanced'),
            "food_style": traveler.get('food', 'casual'),
            "crowd_pref": traveler.get('crowds', 'mixed'),
            "constraints": traveler.get('constraints', []),
            "personal_note": traveler.get('personal_note', ''),
            "budget": data.get('users', {}).get('budget', 'Standard')
        }
        
        print(f"👤 Traveler Profile: {json.dumps(profile, indent=2)}")
        
        # --- 3. GET TIME CONTEXT (using user's local time) ---
        user_local_hour = data.get('context', {}).get('local_hour')
        user_local_time = data.get('context', {}).get('local_time', '')
        time_phase, time_flavor = get_time_context(user_local_hour)
        print(f"🕐 User Local Time: {user_local_time}, Phase: {time_phase}")
        
        # --- 4. REAL-TIME SEARCH ---
        search_context = ""
        if tavily:
            try:
                query = build_search_query(target_city, profile)
                print(f"🔎 Search Query: {query}")
                
                results = tavily.search(query=query, max_results=6)
                if results.get('results'):
                    search_context = json.dumps(results['results'], indent=2)
                    print(f"✅ Found {len(results['results'])} search results")
            except Exception as e:
                print(f"⚠️ Search failed: {e}")
        
        # --- 5. BUILD THE MASTER PROMPT ---
        trip_type = data.get('plan_type', 'NOW')
        
        constraints_text = ""
        if profile['constraints']:
            constraints_text = f"HARD CONSTRAINTS (must respect): {', '.join(profile['constraints'])}"
        
        personal_context = ""
        if profile['personal_note']:
            personal_context = f"PERSONAL CONTEXT: {profile['personal_note']}"
        
        # Late night specific instructions
        late_night_instruction = ""
        if time_phase == "late_night":
            late_night_instruction = """
**LATE NIGHT SPECIAL HANDLING**:
It's currently past midnight. Most places are CLOSED. You have two options:
1. If plan_type is "NOW": ONLY suggest places that are actually open 24 hours or late night (late-night diners, 24h cafes, night markets, after-hours lounges). If nothing is open, acknowledge this and suggest the user rest and plan for tomorrow morning.
2. If plan_type is "TOMORROW": Plan a normal day starting from morning, NOT from late night.

Be honest - don't suggest cafes or restaurants that would be closed at 2-4 AM unless they are specifically known to be open.
"""

        full_prompt = f"""
{SYSTEM_PROMPT}

---
## THIS TRIP'S CONTEXT

**Location**: {target_city}
**Plan Type**: {trip_type}
**User's Current Local Time**: {user_local_time}
**Current Time Phase**: {time_phase} - {time_flavor}
{late_night_instruction}

**Traveler Profile**:
- Group: {profile['group']}
- Occasion: {profile['occasion']}
- Pace: {profile['energy']}
- Food Preference: {profile['food_style']}
- Crowd Preference: {profile['crowd_pref']}
- Budget: {profile['budget']}
{constraints_text}
{personal_context}

**Real-Time Search Results** (use these as primary source):
{search_context if search_context else f"No search results available. Use your internal knowledge of {target_city} to suggest real, specific places."}

---
## INSTRUCTIONS FOR THIS PLAN

Create a {trip_type} itinerary for {target_city}.
- If NOW and it's late_night (after midnight): ONLY suggest 24-hour or late-night spots, or honestly tell them most places are closed
- If NOW: Plan for the next 4-6 hours starting from {time_phase}
- If TOMORROW: Plan a full day starting from morning (ignore current late hour)
- If WEEKEND/TRIP: Plan a full vacation day starting from morning

Remember to:
1. Only suggest REAL places with specific names
2. Match every suggestion to their stated preferences
3. Write in your warm, narrative style
4. Include practical transition details
5. Respect their constraints absolutely
6. BE TIME-AWARE: Don't suggest morning cafes at 2 AM, don't suggest closed restaurants

Output valid JSON only. No markdown. No explanation outside the JSON.
"""
        
        # --- 6. GET AI RESPONSE ---
        print("🤖 Generating itinerary...")
        raw_response = ask_google(full_prompt)
        
        # Robust JSON cleaning function
        def clean_and_parse_json(response_text):
            """Cleans AI response and attempts to parse as JSON."""
            clean = response_text.strip()
            
            # Remove markdown code blocks
            if clean.startswith("```json"):
                clean = clean[7:]
            elif clean.startswith("```"):
                clean = clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
            
            # Try to find JSON object boundaries
            start_idx = clean.find('{')
            end_idx = clean.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                clean = clean[start_idx:end_idx + 1]
            
            # Fix common JSON issues
            clean = clean.replace('\n', ' ').replace('\r', ' ')
            clean = clean.replace('\t', ' ')
            
            return json.loads(clean)
        
        try:
            parsed_response = clean_and_parse_json(raw_response)
        except json.JSONDecodeError as parse_error:
            print(f"⚠️ First JSON parse failed: {parse_error}")
            print("🔄 Retrying with simpler prompt...")
            
            # Retry with a simpler, more constrained prompt
            retry_prompt = f"""
You must respond with ONLY valid JSON. No text before or after.

Create a simple late-night itinerary for {target_city}.

respond with this exact structure (fill in the values):
{{
  "meta": {{
    "greeting": "It's late! Here's what's still open...",
    "summary": "A brief late-night plan or acknowledgment that most places are closed."
  }},
  "timeline": [
    {{
      "time_slot": "02:30 - 03:30",
      "title": "Name of a 24-hour place or suggestion to rest",
      "subtitle": "Brief description",
      "narrative": "Why this is appropriate for late night",
      "tags": ["Late Night", "24 Hours"],
      "google_query": "place name {target_city}"
    }}
  ],
  "closing": {{
    "reflection": "A warm closing thought about late night adventures or getting rest"
  }}
}}

Output ONLY the JSON. No explanation.
"""
            raw_response = ask_google(retry_prompt, temperature=0.3)
            parsed_response = clean_and_parse_json(raw_response)
        
        # --- 7. VALIDATE OUTPUT (pass time_phase for late-night leniency) ---
        issues = validate_itinerary(parsed_response, target_city, time_phase)
        
        if issues:
            print(f"⚠️ Validation Issues: {issues}")
            
            # Force a rewrite
            fix_prompt = f"""
The previous response had these issues: {issues}

REWRITE the itinerary for {target_city} with ONLY real, specific place names.
Do not use generic names like "Local Cafe" or "City Restaurant".
Do not suggest places in the wrong city.

{full_prompt}
"""
            raw_response = ask_google(fix_prompt)
            parsed_response = clean_and_parse_json(raw_response)
        
        print("✅ Itinerary generated successfully!")
        return jsonify(parsed_response)
    
    except json.JSONDecodeError as e:
        print(f"❌ JSON Parse Error: {e}")
        return jsonify({
            "error": "The AI returned an invalid response. Please try again.",
            "details": str(e)
        }), 500
    
    except Exception as e:
        print(f"❌ Server Error: {e}")
        return jsonify({
            "error": str(e)
        }), 500


# --- STARTUP ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"\n🚀 Travel Buddy Pro v2.0 starting on port {port}")
    print("=" * 50)
    app.run(host='0.0.0.0', port=port, debug=False)

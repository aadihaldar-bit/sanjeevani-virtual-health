import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    
    # MongoDB Database Settings (Connect via MongoDB Compass)
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "sanjeevani_healthcare")
    
    # Voice Bot Integration Push Endpoint & Auth
    VOICE_BOT_ENDPOINT_URL: str = os.getenv("VOICE_BOT_ENDPOINT_URL", "http://localhost:8000/api/v1/prescriptions")
    VOICE_BOT_API_KEY: str = os.getenv("VOICE_BOT_API_KEY", "")

    # Medicine Pickup — nearby pharmacies (Geoapify preferred; OpenStreetMap fallback)
    GEOAPIFY_API_KEY: str = os.getenv("GEOAPIFY_API_KEY", "")
    FOURSQUARE_API_KEY: str = os.getenv("FOURSQUARE_API_KEY", "")
    PHARMACY_SEARCH_RADIUS_M: int = int(os.getenv("PHARMACY_SEARCH_RADIUS_M", "4000"))
    NOMINATIM_USER_AGENT: str = os.getenv("NOMINATIM_USER_AGENT", "sanjeevani-virtual-health/1.0")

settings = Settings()

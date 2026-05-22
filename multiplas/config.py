import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BETNACIONAL_API_URL = os.getenv("BETNACIONAL_API_URL", "http://betnacional-client:8001")
    NUM_LEGS = int(os.getenv("NUM_LEGS", "3"))
    STAKE = float(os.getenv("STAKE", "1.00"))
    TIMEOUT = int(os.getenv("TIMEOUT", "30"))
